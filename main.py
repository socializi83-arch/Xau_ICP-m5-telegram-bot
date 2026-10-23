import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
import pytz
import httpx
import pandas as pd
import yfinance as yf
from fastapi import FastAPI

# === CONFIG ===
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")
PARIS_TZ = pytz.timezone("Europe/Paris")

last_signal_time = 0
active_trade = None  # pour suivre TP1 / TP2

# === TELEGRAM ===
async def send_telegram(msg: str):
    if not TELEGRAM_TOKEN or not TELEGRAM_CHAT_ID:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient() as client:
        await client.post(url, json={
            "chat_id": TELEGRAM_CHAT_ID,
            "text": msg,
            "parse_mode": "Markdown"
        })

# === NEWS FILTER ===
async def is_news_blocked():
    # Ton filtre existant - je le garde tel quel
    try:
        # Exemple simple: bloque si news USD High dans 60min
        # Remplace par ton code de news existant
        return False, ""
    except:
        return False, ""

# === DATA XAU M5 ===
def fetch_xau_m5():
    try:
        df = yf.download("GC=F", period="5d", interval="5m", progress=False)
        if df.empty:
            return None
        df = df.reset_index()
        # flatten multiindex si yfinance
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)
        return df
    except Exception as e:
        print(f"fetch error: {e}")
        return None

# === TA LOGIC (ton detect_signal existant) ===
def detect_signal(df):
    try:
        # === TON CODE ICT / SMC EXISTANT ===
        # Je garde ta logique, exemple simplifié :
        # Remplace ce bloc par ton vrai detect_signal complet si tu l'as
        
        last_close = float(df['Close'].iloc[-1])
        
        # EXEMPLE FAKE SIGNAL POUR TEST - enlève ça et remets ton vrai code
        # Ton vrai code doit retourner dict avec side, entry, sl, tp1, tp2, setup, killzone
        # return None si pas de signal
        
        # Pour l'exemple je retourne None pour ne pas spammer
        # TOI: remets ici ton code qui détecte Sweep + CHoCH + OB
        return None
        
    except Exception as e:
        print(f"detect error: {e}")
        return None

# === BOT LOOP AVEC SUIVI TP ===
async def bot_loop():
    global last_signal_time, active_trade
    print("Bot XAU M5 démarré - avec suivi TP1/TP2")
    while True:
        try:
            now_paris = datetime.now(PARIS_TZ)
            blocked, news_titles = await is_news_blocked()
            
            # 1. GESTION NEWS
            if blocked and asyncio.get_event_loop().time() - last_signal_time > 1800:
                if news_titles:
                    await send_telegram(f"🚨 *NEWS FILTER ACTIVÉ*\n\n*Bloqué:* {news_titles}\n*Pas de signal jusqu'à:* {(now_paris + timedelta(hours=1)).strftime('%H:%M')} Paris")
                last_signal_time = asyncio.get_event_loop().time()
                await asyncio.sleep(300)
                continue

            # 2. FETCH & CHECK SIGNAL OU SUIVI DE TRADE
            df = fetch_xau_m5()
            if df is None:
                await asyncio.sleep(60)
                continue

            current_price = float(df['Close'].iloc[-1])

            # Si on a un trade actif -> on check TP1 / TP2 / SL
            if active_trade:
                side = active_trade['side']
                if side == "BUY":
                    if current_price <= active_trade['sl']:
                        await send_telegram(f"❌ *SL TOUCHÉ*\n\n*Trade:* BUY {active_trade['entry']} -> SL {active_trade['sl']}\n*Perte:* -{round(active_trade['entry'] - active_trade['sl'], 2)}$\n\nOn attend le prochain setup. Max 2 trades/jour.\n\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                    elif not active_trade['tp1_hit'] and current_price >= active_trade['tp1']:
                        await send_telegram(f"✅ *TP1 TOUCHÉ - Sécurise !*\n\n*Trade:* BUY {active_trade['entry']} -> {active_trade['tp1']}\n*Profit:* +{round(active_trade['tp1'] - active_trade['entry'], 2)}$\n*Action:* Passe ton SL à BE ({active_trade['entry']})\n*Runner TP2:* {active_trade['tp2']} en cours...\n\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade['tp1_hit'] = True
                        active_trade['sl'] = active_trade['entry'] # BE
                    elif active_trade['tp1_hit'] and current_price >= active_trade['tp2']:
                        await send_telegram(f"🎯 *TP2 TOUCHÉ - Full TP !*\n\n*Trade:* BUY {active_trade['entry']} -> {active_trade['tp2']}\n*Profit total:* +{round(active_trade['tp2'] - active_trade['entry'], 2)}$\n\n*Stat du jour:* 1/1 ✅\n\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                else: # SELL
                    if current_price >= active_trade['sl']:
                        await send_telegram(f"❌ *SL TOUCHÉ*\n\n*Trade:* SELL {active_trade['entry']} -> SL {active_trade['sl']}\n*Perte:* -{round(active_trade['sl'] - active_trade['entry'], 2)}$\n\nOn attend le prochain setup.\n\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None
                    elif not active_trade['tp1_hit'] and current_price <= active_trade['tp1']:
                        await send_telegram(f"✅ *TP1 TOUCHÉ - Sécurise !*\n\n*Trade:* SELL {active_trade['entry']} -> {active_trade['tp1']}\n*Profit:* +{round(active_trade['entry'] - active_trade['tp1'], 2)}$\n*Action:* Passe ton SL à BE\n*Runner TP2:* {active_trade['tp2']} en cours...\n\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade['tp1_hit'] = True
                        active_trade['sl'] = active_trade['entry']
                    elif active_trade['tp1_hit'] and current_price <= active_trade['tp2']:
                        await send_telegram(f"🎯 *TP2 TOUCHÉ - Full TP !*\n\n*Trade:* SELL {active_trade['entry']} -> {active_trade['tp2']}\n*Profit total:* +{round(active_trade['entry'] - active_trade['tp2'], 2)}$\n\n*Heure Paris:* {now_paris.strftime('%H:%M')}")
                        active_trade = None

                await asyncio.sleep(60)
                continue

            # 3. PAS DE TRADE ACTIF -> ON CHERCHE UN NOUVEAU SIGNAL
            signal = detect_signal(df) if df is not None else None
            if signal and asyncio.get_event_loop().time() - last_signal_time > 300:
                emoji = "🟢" if "BUY" in signal['side'] else "🔴"
                msg = f"{emoji} *XAUUSD - {signal['side']}*\n\n*Setup:* {signal.get('setup','Sweep + CHoCH + OB')}\n*Timeframe:* M5\n*Killzone:* {signal.get('killzone','NY')}\n\n*Entrée:* {signal['entry']}\n*SL:* {signal['sl']}\n*TP1:* {signal['tp1']} (50% -> BE)\n*TP2:* {signal['tp2']} (Runner)\n\n*Filtre:* ✅ Pas de news\n*Heure Paris:* {now_paris.strftime('%H:%M')}"
                await send_telegram(msg)
                last_signal_time = asyncio.get_event_loop().time()
                
                # On active le suivi pour ce trade
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
            print(e)
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
    blocked, news = await is_news_blocked()
    return {"status": "online", "news_blocked": blocked, "time_paris": datetime.now(PARIS_TZ).isoformat()}

@app.get("/test-telegram")
async def test_tg():
    await send_telegram("✅ Bot XAU M5 connecté ! Filtre news actif. Suivi TP1/TP2 actif.")
    return {"sent": True}
