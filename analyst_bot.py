"""
📈 Morning Financial Trend Analyst Bot
Detects early-stage sector trends on US stock markets using Claude AI.
"""

import os
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import anthropic
import yfinance as yf
import pandas as pd

# ── CONFIG ──────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]  # comma-separated list
SENDER_EMAIL    = os.environ["SENDER_EMAIL"]       # Gmail address
SENDER_PASSWORD = os.environ["SENDER_PASSWORD"]    # Gmail App Password
ANTHROPIC_KEY   = os.environ["ANTHROPIC_API_KEY"]

# Sectors to track — ETFs as proxies for sector health
SECTOR_ETFS = {
    "Semiconductors":       "SOXX",
    "AI / Cloud":           "AIQ",
    "Clean Energy":         "ICLN",
    "Biotech":              "XBI",
    "Cybersecurity":        "HACK",
    "Space & Defense":      "ITA",
    "Quantum Computing":    "QTUM",
    "Robotics & Automation":"ROBO",
    "Fintech":              "FINX",
    "Rare Earth & Materials":"REMX",
}

# Individual high-momentum tickers to watch
WATCHLIST = [
    # Semis
    "NVDA", "AMD", "AVGO", "TSM", "ASML",
    # AI infra
    "MSFT", "GOOGL", "META", "AMZN",
    # Emerging
    "IONQ", "RGTI",   # Quantum
    "PLTR", "AI",     # AI software
    "SMCI",           # AI servers
    "CEG", "VST",     # Nuclear / power infra
    "MSTR",           # Bitcoin proxy
]


# ── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_ticker_data(ticker: str, days: int = 30) -> dict:
    """Fetch price, volume, RSI-like momentum for a ticker."""
    try:
        tk = yf.Ticker(ticker)
        hist = tk.history(period=f"{days}d")
        if hist.empty:
            return {}

        current   = hist["Close"].iloc[-1]
        prev_week = hist["Close"].iloc[-5] if len(hist) >= 5 else hist["Close"].iloc[0]
        prev_month= hist["Close"].iloc[0]
        avg_vol   = hist["Volume"].mean()
        last_vol  = hist["Volume"].iloc[-1]

        return {
            "ticker":        ticker,
            "price":         round(current, 2),
            "change_1w_%":   round((current - prev_week) / prev_week * 100, 2),
            "change_1m_%":   round((current - prev_month) / prev_month * 100, 2),
            "volume_ratio":  round(last_vol / avg_vol, 2),   # >1.5 = unusual activity
            "52w_high":      round(hist["Close"].max(), 2),
            "near_52w_high": bool(current >= hist["Close"].max() * 0.95),
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def fetch_all_data() -> dict:
    """Fetch data for all sectors and watchlist tickers."""
    print("📡 Fetching market data...")

    sectors = {}
    for name, etf in SECTOR_ETFS.items():
        sectors[name] = fetch_ticker_data(etf)

    tickers = {}
    for t in WATCHLIST:
        tickers[t] = fetch_ticker_data(t)

    return {"sectors": sectors, "tickers": tickers, "date": datetime.now().strftime("%Y-%m-%d")}


# ── AI ANALYSIS ──────────────────────────────────────────────────────────────

def analyze_with_claude(market_data: dict) -> str:
    """Send market data to Claude for trend analysis."""
    print("🤖 Running Claude analysis...")

    client = anthropic.Anthropic(api_key=ANTHROPIC_KEY)

    prompt = f"""
You are a sharp financial analyst specializing in identifying EARLY-STAGE sector trends on US markets —
the kind of trends that were visible in semiconductors in 2019-2020 BEFORE the mainstream caught on.

Today is {market_data['date']}.

Here is fresh market data:

SECTOR ETFs (proxies for sector momentum):
{json.dumps(market_data['sectors'], indent=2)}

INDIVIDUAL TICKERS (watchlist):
{json.dumps(market_data['tickers'], indent=2)}

Your task — produce a concise morning briefing with:

1. 🔥 TOP EMERGING TREND (1-2 sectors showing unusual early momentum)
   - What signal are you seeing? (volume spike, consecutive gains, near 52w high, etc.)
   - What's the narrative / macro catalyst behind it?
   - Specific tickers to watch (from watchlist OR well-known names in that sector)
   - Risk level: LOW / MEDIUM / HIGH

2. 📊 SECTOR SCORECARD
   - Rank all sectors: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   - One sentence per sector

3. 💡 EARLY RADAR (1-2 themes that are too early to call but worth watching)
   - Subtle signals you'd want to monitor over the next 2-4 weeks

4. ⚠️ RISKS THIS WEEK
   - Macro events, earnings, or technicals that could disrupt momentum

Keep it punchy. No fluff. Think like a hedge fund analyst writing for a partner who reads this in 3 minutes over morning coffee.
Format using emojis and clear sections. No disclaimers needed.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ── EMAIL DELIVERY ───────────────────────────────────────────────────────────

def send_email(analysis: str, date: str):
    """Send the morning briefing by email."""
    print("📧 Sending email...")

    msg = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Morning Market Brief — {date}"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)

    # Plain text version
    text_part = MIMEText(analysis, "plain")

    # HTML version (nicer formatting)
    html_body = analysis.replace("\n", "<br>")
    html = f"""
    <html><body style="font-family: 'Georgia', serif; max-width: 680px; margin: auto;
                        background: #0d0d0d; color: #e8e8e8; padding: 32px;">
      <div style="border-left: 4px solid #00ff88; padding-left: 20px; margin-bottom: 24px;">
        <h1 style="color: #00ff88; font-size: 22px; margin: 0;">📈 Morning Market Brief</h1>
        <p style="color: #888; margin: 4px 0 0;">{date}</p>
      </div>
      <div style="line-height: 1.8; font-size: 15px;">
        {html_body}
      </div>
      <hr style="border-color: #333; margin-top: 40px;">
      <p style="color: #555; font-size: 12px;">Generated by your Financial Trend Bot · Not financial advice</p>
    </body></html>
    """
    html_part = MIMEText(html, "html")

    msg.attach(text_part)
    msg.attach(html_part)

    with smtplib.SMTP_SSL("smtp.gmail.com", 465) as server:
        server.login(SENDER_EMAIL, SENDER_PASSWORD)
        server.sendmail(SENDER_EMAIL, RECIPIENT_EMAILS, msg.as_string())

    print("✅ Email sent!")


# ── MAIN ─────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🚀 Financial Trend Bot starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    market_data = fetch_all_data()
    analysis    = analyze_with_claude(market_data)
    send_email(analysis, market_data["date"])

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()