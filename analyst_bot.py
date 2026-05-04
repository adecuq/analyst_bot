"""
📈 Morning Financial Trend Analyst Bot
Detects early-stage sector trends on US stock markets using Claude AI.
Includes persistent memory across runs to track evolving signals.
"""

import os
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, date, timedelta
import anthropic
import yfinance as yf
import pandas as pd

# ── CONFIG ──────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]
SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD  = os.environ["SENDER_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

MEMORY_FILE      = "memory.json"
MEMORY_DAYS      = 30   # how many days of history to keep

# Sectors to track — ETFs as proxies for sector health
SECTOR_ETFS = {
    "Semiconductors":        "SOXX",
    "AI / Cloud":            "AIQ",
    "Clean Energy":          "ICLN",
    "Biotech":               "XBI",
    "Cybersecurity":         "HACK",
    "Space & Defense":       "ITA",
    "Quantum Computing":     "QTUM",
    "Robotics & Automation": "ROBO",
    "Fintech":               "FINX",
    "Rare Earth & Materials":"REMX",
}

# Individual high-momentum tickers to watch
WATCHLIST = [
    "NVDA", "AMD", "AVGO", "TSM", "ASML",
    "MSFT", "GOOGL", "META", "AMZN",
    "IONQ", "RGTI",
    "PLTR", "AI",
    "SMCI",
    "CEG", "VST",
    "MSTR",
]


# ── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_ticker_data(ticker: str, days: int = 30) -> dict:
    """Fetch price, volume, momentum for a ticker."""
    try:
        tk   = yf.Ticker(ticker)
        hist = tk.history(period=f"{days}d")
        if hist.empty:
            return {}

        current    = hist["Close"].iloc[-1]
        prev_week  = hist["Close"].iloc[-5] if len(hist) >= 5 else hist["Close"].iloc[0]
        prev_month = hist["Close"].iloc[0]
        avg_vol    = hist["Volume"].mean()
        last_vol   = hist["Volume"].iloc[-1]

        return {
            "ticker":        ticker,
            "price":         round(float(current), 2),
            "change_1w_%":   round(float((current - prev_week) / prev_week * 100), 2),
            "change_1m_%":   round(float((current - prev_month) / prev_month * 100), 2),
            "volume_ratio":  round(float(last_vol / avg_vol), 2),
            "52w_high":      round(float(hist["Close"].max()), 2),
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

    return {
        "sectors": sectors,
        "tickers": tickers,
        "date":    datetime.now().strftime("%Y-%m-%d"),
    }


# ── MEMORY ───────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    """Load historical signal memory from file."""
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def extract_signals(market_data: dict) -> dict:
    """Extract key signals worth tracking over time."""
    signals = {}

    for sector, data in market_data["sectors"].items():
        if not data or "error" in data:
            continue
        signals[sector] = {
            "change_1w_%":   data.get("change_1w_%"),
            "change_1m_%":   data.get("change_1m_%"),
            "volume_ratio":  data.get("volume_ratio"),
            "near_52w_high": data.get("near_52w_high"),
        }

    notable_tickers = {}
    for ticker, data in market_data["tickers"].items():
        if not data or "error" in data:
            continue
        # Only save tickers with notable movement
        if abs(data.get("change_1w_%", 0)) >= 5 or data.get("volume_ratio", 0) >= 1.5:
            notable_tickers[ticker] = {
                "change_1w_%":   data.get("change_1w_%"),
                "change_1m_%":   data.get("change_1m_%"),
                "volume_ratio":  data.get("volume_ratio"),
                "near_52w_high": data.get("near_52w_high"),
            }

    return {"sectors": signals, "notable_tickers": notable_tickers}


def save_memory(market_data: dict, analysis: str):
    """Save today's signals and a short summary to memory."""
    memory = load_memory()
    today  = market_data["date"]

    memory[today] = {
        "signals":       extract_signals(market_data),
        "brief_summary": analysis[:600],
    }

    # Keep only last MEMORY_DAYS days
    cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).strftime("%Y-%m-%d")
    memory = {k: v for k, v in memory.items() if k >= cutoff}
    memory = dict(sorted(memory.items()))

    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

    print(f"💾 Memory saved ({len(memory)} days of history)")


def build_memory_context(memory: dict) -> str:
    """Format memory into a readable context string for Claude."""
    if not memory:
        return "No historical data yet — this is the first run."

    lines = ["HISTORICAL SIGNAL TRACKER (last 30 trading days):\n"]

    # Per-sector trend table (last 10 days)
    all_sectors = set()
    for day_data in memory.values():
        all_sectors.update(day_data.get("signals", {}).get("sectors", {}).keys())

    lines.append("── Sector momentum over time (change_1w_%) ──")
    for sector in sorted(all_sectors):
        row = f"{sector:30s}"
        for day, day_data in sorted(memory.items())[-10:]:
            val = day_data.get("signals", {}).get("sectors", {}).get(sector, {}).get("change_1w_%")
            row += f"  {day[5:]}:{val:+.1f}%" if val is not None else "         —"
        lines.append(row)

    # Notable ticker history
    lines.append("\n── Notable ticker appearances ──")
    ticker_history = {}
    for day, day_data in sorted(memory.items()):
        for ticker, tdata in day_data.get("signals", {}).get("notable_tickers", {}).items():
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append({"date": day, **tdata})

    for ticker, appearances in sorted(ticker_history.items()):
        latest = appearances[-1]
        lines.append(
            f"{ticker}: on radar {len(appearances)} day(s) | "
            f"latest 1w={latest.get('change_1w_%', 0):+.1f}% "
            f"vol={latest.get('volume_ratio', 0):.2f}x "
            f"near_52wh={latest.get('near_52w_high')}"
        )

    # Recent daily summaries
    lines.append("\n── Recent daily summaries ──")
    for day, day_data in sorted(memory.items())[-5:]:
        summary = day_data.get("brief_summary", "")[:200]
        lines.append(f"\n{day}:\n{summary}...")

    return "\n".join(lines)


# ── AI ANALYSIS ──────────────────────────────────────────────────────────────

def analyze_with_claude(market_data: dict, memory: dict) -> str:
    """Send market data + memory to Claude for trend analysis."""
    print("🤖 Running Claude analysis...")

    client         = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    memory_context = build_memory_context(memory)

    prompt = f"""
You are a sharp financial analyst specializing in identifying EARLY-STAGE sector trends on US markets —
the kind of trends that were visible in semiconductors in 2019-2020 BEFORE the mainstream caught on.

Today is {market_data['date']}.

═══════════════════════════════════════════
FRESH MARKET DATA (today)
═══════════════════════════════════════════

SECTOR ETFs:
{json.dumps(market_data['sectors'], indent=2)}

INDIVIDUAL TICKERS:
{json.dumps(market_data['tickers'], indent=2)}

═══════════════════════════════════════════
HISTORICAL MEMORY (past 30 days)
═══════════════════════════════════════════
{memory_context}

═══════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════

Use BOTH today's data AND historical memory to produce a morning briefing.
The memory is your edge — use it to track how signals evolve over time.

1. 🔥 TOP EMERGING TREND (1-2 sectors)
   - Signal + how long it has been building (reference history)
   - Narrative / macro catalyst
   - Tickers to watch
   - Risk level: LOW / MEDIUM / HIGH

2. 📊 SECTOR SCORECARD
   - All sectors: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   - Flag if trend is NEW (first time) vs CONFIRMED (3+ days) vs FADING

3. 🔁 SIGNAL UPDATES (memory-powered follow-ups)
   - Explicitly follow up on signals flagged in previous days
   - "X days ago we flagged Y — here's what happened"
   - Did FINX volume confirm or fade? Did NVDA catch up to semis?
   - Resolve any "check again in N days" items from past briefs

4. 💡 EARLY RADAR
   - Signals still developing — how many days on watch, what to look for

5. ⚠️ RISKS THIS WEEK

Punchy. 3-minute read. Use emojis and clear sections. No disclaimers.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=1800,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ── EMAIL DELIVERY ───────────────────────────────────────────────────────────

def send_email(analysis: str, run_date: str):
    """Send the morning briefing by email."""
    print("📧 Sending email...")

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Morning Market Brief — {run_date}"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)

    text_part = MIMEText(analysis, "plain")
    html_body = analysis.replace("\n", "<br>")
    html = f"""
    <html><body style="font-family: 'Georgia', serif; max-width: 680px; margin: auto;
                        background: #0d0d0d; color: #e8e8e8; padding: 32px;">
      <div style="border-left: 4px solid #00ff88; padding-left: 20px; margin-bottom: 24px;">
        <h1 style="color: #00ff88; font-size: 22px; margin: 0;">📈 Morning Market Brief</h1>
        <p style="color: #888; margin: 4px 0 0;">{run_date}</p>
      </div>
      <div style="line-height: 1.8; font-size: 15px;">{html_body}</div>
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

    memory = load_memory()
    print(f"📚 Loaded {len(memory)} days of memory")

    market_data = fetch_all_data()
    analysis    = analyze_with_claude(market_data, memory)

    save_memory(market_data, analysis)
    send_email(analysis, market_data["date"])

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()