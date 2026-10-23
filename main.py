import os
import asyncio
import httpx
import yfinance as yf
import pandas as pd
from fastapi import FastAPI
from datetime import datetime
import pytz
from contextlib import asynccontextmanager

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN")
CHAT_ID = os.getenv("CHAT_ID")
PARIS = pytz.timezone("Europe/Paris")
FF_URL = "https://nfs.faireconomy.media/ff_calendar_thisweek.json"

last_signal_time = 0

async def send_telegram(text: str):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    async with httpx.AsyncClient(timeout=5) as client:
        await client.post(url, json={"chat_id": CHAT_ID, "text": text, "parse_mode": "Markdown"})

async def get_high_impact_news():
    try:
        async with httpx.AsyncClient(timeout=5) as client:
            r = await client.get(FF_URL)
            data = r.json()
            now = datetime.now(pytz.utc)
            upcoming = []
            for event in data:
                if event.get("impact") != "High": continue
                if event.get("currency") not in ["USD", "ALL"]: continue
                try:
                    dt = datetime.fromisoformat(event.get("date").replace("Z", "+00:00"))
                    delta = (dt - now).total_seconds() / 60
                    if -30 <= delta <= 60:
                        upcoming.append(event)
                except: continue
            return upcoming
    except: return []

async def is_news_blocked():
    news = await get_high_impact_news()
    if news:
        titles = ", ".join([f"{n.get('title')} à {n.get('date')}" for n in news[:2]])
        return True, titles
    return False, ""

def fetch_xau_m5():
    df = yf.download("GC=F", period="5d", interval="5m", progress=False)
    if df.empty: return None
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    return df.dropna()

def detect_signal(df: pd.DataFrame):
    if len(df) < 50: return None
    last = df.iloc[-1]
    prev20_low = df['Low'].iloc[-21:-1].min()
    prev20_high = df['High'].iloc[-21:-1].max()
    prev10_high = df['High'].iloc[-11:-1].max()
    prev10_low = df['Low'].iloc[-11:-1].min()
    
    sweep_low = float(last['Low']) < float(prev20_low) and float(last['Close']) > float(prev20_low)
    sweep_high = float(last['High']) > float(prev20_high) and float(last['Close']) < float(prev20_high)
    bull_choch = float(last['Close']) > float(prev10_high)
    bear_choch = float(last['Close']) < float(prev10_low)

    now = datetime.now(PARIS)
    h,m = now.hour, now.minute
    in_kz = (9 <= h < 11) or (h == 14 and m >= 30) or (15 <= h < 17)
    if not in_kz: return None
    kz_name = "LONDRES" if 9 <= h < 11 else "NEW YORK"
    ob_low = float(df['Low'].iloc[-3:].min())
    ob_high = float(df['High'].iloc[-3:].max())

    if sweep_low and bull_choch:
        entry = ob_low + 0.5
        return {"side": "BUY LIMIT", "entry": round(entry,2), "sl": round(entry-3,2), "tp1": round(entry+6,2), "tp2": round(entry+12,2), "setup": f"Sweep Low + CHoCH Bull", "kz": kz_name}
    if sweep_high and bear_choch:
        entry = ob_high - 0.5
        return {"side": "SELL LIMIT", "entry": round(entry,2), "sl": round(entry+3,2), "tp1": round(entry-6,2), "tp2": round(entry-12,2), "setup": f"Sweep High + CHoCH Bear", "kz": kz_name}
    return None

async def bot_loop():
    global last_signal_time
    await asyncio.sleep(10)
    while True:
        try:
            blocked, news_titles = await is_news_blocked()
            if blocked:
                if asyncio.get_event_loop().time() - last_signal_time > 3600:
                    await send_telegram(f"🚨 *NEWS FILTER ACTIVÉ*\nPas de trade 60min\n{news_titles}")
                    last_signal_time = asyncio.get_event_loop().time()
                await asyncio.sleep(300)
                continue
            df = fetch_xau_m5()
            signal = detect_signal(df) if df is not None else None
            if signal and asyncio.get_event_loop().time() - last_signal_time > 900:
                emoji = "🟢" if "BUY" in signal['side'] else "🔴"
                msg = f"{emoji} *XAUUSD - {signal['side']}*\n\n*Setup:* `{signal['setup']}`\n*Killzone:* {signal['kz']} - {datetime.now(PARIS).strftime('%H:%M:%S')} Paris\n*News:* ✅ Aucune news High\n\n*ENTRÉE:* `{signal['entry']}` (LIMIT)\n*SL:* `{signal['sl']}` (3$)\n*TP1:* `{signal['tp1']}` -> 50%\n*TP2:* `{signal['tp2']}` -> BE à TP1\n\nConsigne: 0.5% risque max."
                await send_telegram(msg)
                last_signal_time = asyncio.get_event_loop().time()
            await asyncio.sleep(60)
        except Exception as e:
            print(e)
            await asyncio.sleep(60)

@asynccontextmanager
async def lifespan(app: FastAPI):
    task = asyncio.create_task(bot_loop())
    yield
    task.cancel()

app = FastAPI(lifespan=lifespan)

@app.api_route("/", methods=["GET", "HEAD", "POST"])
async def home():
    blocked, news = await is_news_blocked()
    return {"status": "online", "news_blocked": blocked, "time_paris": datetime.now(PARIS).isoformat()}

@app.get("/test-telegram")
async def test_tg():
    await send_telegram("✅ Bot XAU M5 connecté ! Filtre news actif.")
    return {"sent": True}
