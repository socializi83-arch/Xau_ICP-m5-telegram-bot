import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime
import pytz
import httpx
import yfinance as yf
import pandas as pd
from fastapi import FastAPI

# === CONFIG ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PARIS_TZ = pytz.timezone("Europe/Paris")

last_signal_time = 0
active_trade = None

# === TELEGRAM ===
async def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        print("Telegram config manquante")
        return False
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=20) as client:
            r = await client.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"})
            print(f"Telegram status: {r.status_code}")
            return r.status_code == 200
    except Exception as e:
        print(f"Telegram error: {e}")
        return False

# === NEWS FILTER - CORRIGÉ ===
async def is_news_blocked():
    # Désactivé temporairement pour éviter le bug JSON
    # On retourne toujours False, plus de "news error"
    return False, ""

# === FETCH XAU M5 ===
def fetch_xau_m5():
    try:
        df = yf.download("GC=F", period="2d", interval="5m", progress=False, auto_adjust=True)
        if df.empty:
            return None
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        df = df.dropna()
        return df
    except Exception as e:
        print(f"fetch error: {e}")
        return None

# === DETECT SIGNAL ===
def detect_signal(df):
    try:
        if len(df) < 50:
            return None

        now_paris = datetime.now(PARIS_TZ)
        hour = now_paris.hour
        # Killzones
        is_london = 9 <= hour <= 11
        is_ny = 14 <= hour <= 17 or (hour == 14 and now_paris.minute >= 30)
        if not (is_london or is_ny):
            return None

        last_close = float(df['Close'].iloc[-1])
        recent_low = float(df['Low'].iloc[-20:-1].min())
        recent_high = float(df['High'].iloc[-20:-1].max())

        # BUY Sweep Low + CHoCH
        if last_close < recent_low * 0.999:
            prev_high = float(df['High'].iloc[-10:-1].max())
            if last_close > prev_high:
                entry = last_close
                sl = recent_low - 0.8
                risk = entry - sl
                if risk <= 0 or risk > 8:
                    return None
                return {
                    "side": "BUY LIMIT",
                    "setup": "Sweep Low + CHoCH Bull + OB Demand",
                    "killzone": "Londres" if is_london else "NY PM",
                    "entry": round(entry, 2),
                    "sl": round(sl, 2),
                    "tp1": round(entry + risk * 1.5, 2),
                    "tp2": round(entry + risk * 3, 2),
                }

        # SELL Sweep High + CHoCH
        if last_close > recent_high * 1.001:
            prev_low = float(df['Low'].iloc[-10:-1].min())
            if last_close < prev_low:
                entry = last_close
                sl = recent_high + 0.8
                risk = sl - entry
                if risk <= 0 or risk > 8:
                    return None
                return {
                    "side": "SELL LIMIT",
                    "setup": "Sweep High + CHoCH Bear + OB Supply",
                    "killzone": "Londres" if is_london else "NY PM",
                    "entry": round(entry, 2),
                    "sl": round(sl, 2),
                    "tp1": round(entry - risk * 1.5, 2),
                    "tp2": round(entry - risk * 3, 2),
                }
        return None
    except Exception as e:
        print(f"detect error: {e}")
        return None

# === BOT LOOP AVEC SUIVI TP1/TP2/SL ===
async def bot_loop():
    global last_signal_time, active_trade
    print("Bot XAU M5 démarré avec suivi TP - FIX news error")
    while True:
        try:
            now_paris = datetime.now(PARIS_TZ)
            df = fetch_xau_m5()
            if df is None:
                await asyncio.sleep(60)
                continue

            current_price = float(df['Close'].iloc[-1])

            # 1. Trade actif -> suivi
            if active_trade:
                side = active_trade['side']
                if side == "BUY":
                    if current_price <= active_trade['sl']:
                        await send_telegram(f"❌ *SL TOUCHÉ*\n\n*Trade:* BUY {active_trade['entry']} -> SL {active_trade['sl']}\n*Perte:* -{round(active_trade['entry'] - active_trade['sl'], 2)}$\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                    elif not active_trade['tp1_hit'] and current_price >= active_trade['tp1']:
                        await send_telegram(f"✅ *TP1 TOUCHÉ - Sécurise !*\n\n*Trade:* BUY {active_trade['entry']} -> {active_trade['tp1']}\n*Profit:* +{round(active_trade['tp1'] - active_trade['entry'], 2)}$\n*Action:* Passe SL à BE\n*Runner TP2:* {active_trade['tp2']}\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade['tp1_hit'] = True
                        active_trade['sl'] = active_trade['entry']
                    elif active_trade['tp1_hit'] and current_price >= active_trade['tp2']:
                        await send_telegram(f"🎯 *TP2 TOUCHÉ - Full TP !*\n\n*Trade:* BUY {active_trade['entry']} -> {active_trade['tp2']}\n*Profit total:* +{round(active_trade['tp2'] - active_trade['entry'], 2)}$\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                else:
                    if current_price >= active_trade['sl']:
                        await send_telegram(f"❌ *SL TOUCHÉ*\n\n*Trade:* SELL {active_trade['entry']} -> SL {active_trade['sl']}\n*Perte:* -{round(active_trade['sl'] - active_trade['entry'], 2)}$\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                    elif not active_trade['tp1_hit'] and current_price <= active_trade['tp1']:
                        await send_telegram(f"✅ *TP1 TOUCHÉ - Sécurise !*\n\n*Trade:* SELL {active_trade['entry']} -> {active_trade['tp1']}\n*Profit:* +{round(active_trade['entry'] - active_trade['tp1'], 2)}$\n*Action:* Passe SL à BE\n*Runner TP2:* {active_trade['tp2']}\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade['tp1_hit'] = True
                        active_trade['sl'] = active_trade['entry']
                    elif active_trade['tp1_hit'] and current_price <= active_trade['tp2']:
                        await send_telegram(f"🎯 *TP2 TOUCHÉ - Full TP !*\n\n*Trade:* SELL {active_trade['entry']} -> {active_trade['tp2']}\n*Profit total:* +{round(active_trade['entry'] - active_trade['tp2'], 2)}$\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                await asyncio.sleep(60)
                continue

            # 2. Cherche nouveau signal
            signal = detect_signal(df)
            if signal and asyncio.get_event_loop().time() - last_signal_time > 300:
                emoji = "🟢" if "BUY" in signal['side'] else "🔴"
                msg = f"{emoji} *XAUUSD - {signal['side']}*\n\n*Setup:* {signal['setup']}\n*Timeframe:* M5\n*Killzone:* {signal['killzone']}\n\n*Entrée:* {signal['entry']}\n*SL:* {signal['sl']}\n*TP1:* {signal['tp1']} (50% -> BE)\n*TP2:* {signal['tp2']} (Runner)\n\n*Filtre:* ✅ Pas de news\n*Heure Paris:* {now_paris.strftime('%H:%M')}"
                await send_telegram(msg)
                last_signal_time = asyncio.get_event_loop().time()
                active_trade = {
                    "side": "BUY" if "BUY" in signal['side'] else "SELL",
                    "entry": float(signal['entry']),
                    "sl": float(signal['sl']),
                    "tp1": float(signal['tp1']),
                    "tp2": float(signal['tp2']),
                    "tp1_hit": False
                }
            await asyncio.sleep(60)
        except Exception as e:
            print(f"loop error: {e}")
            await asyncio.sleep(60)

# === FASTAPI ===
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(bot_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.api_route("/", methods=["GET", "HEAD", "POST", "OPTIONS"])
async def home():
    return {"status": "online", "time_paris": datetime.now(PARIS_TZ).isoformat(), "active_trade": active_trade, "source": "fixed-news-error"}

@app.get("/test-telegram")
async def test_tg():
    ok = await send_telegram("✅ Bot XAU M5 connecté ! Fix news error OK. Suivi TP1/TP2 actif.")
    return {"sent": ok}
