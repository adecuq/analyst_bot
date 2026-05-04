"""
📈 Morning Financial Trend Analyst Bot
Detects early-stage sector trends on US stock markets + crypto top 100.
Includes persistent memory across runs to track evolving signals.
"""

import os
import smtplib
import json
import requests
import time
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import anthropic
import yfinance as yf

# ── CONFIG ──────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]
SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD  = os.environ["SENDER_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

MEMORY_FILE  = "memory.json"
MEMORY_DAYS  = 180  # 6 months — weekly timeframe analysis

# ── STOCK: Sector ETFs ───────────────────────────────────────────────────────
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

WATCHLIST = [
    "NVDA", "AMD", "AVGO", "TSM", "ASML",
    "MSFT", "GOOGL", "META", "AMZN",
    "IONQ", "RGTI",
    "PLTR", "AI",
    "SMCI",
    "CEG", "VST",
    "MSTR",
]

# ── CRYPTO: CoinGecko category IDs ──────────────────────────────────────────
# These map to CoinGecko's /coins/categories endpoint
CRYPTO_CATEGORIES = {
    "Layer 1":           "layer-1",
    "Layer 2":           "layer-2",
    "DeFi":              "decentralized-finance-defi",
    "Meme Coins":        "meme-token",
    "AI Crypto":         "artificial-intelligence",
    "RWA":               "real-world-assets-rwa",
    "Gaming / GameFi":   "gaming",
    "Stablecoins":       "stablecoins",
    "DEX":               "decentralized-exchange",
    "Liquid Staking":    "liquid-staking-tokens",
}


# ── STOCK DATA FETCHING ──────────────────────────────────────────────────────

def fetch_ticker_data(ticker: str, days: int = 30) -> dict:
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


def fetch_all_stock_data() -> dict:
    print("📡 Fetching stock market data...")
    sectors = {name: fetch_ticker_data(etf) for name, etf in SECTOR_ETFS.items()}
    tickers = {t: fetch_ticker_data(t) for t in WATCHLIST}
    return {"sectors": sectors, "tickers": tickers}


# ── CRYPTO DATA FETCHING ─────────────────────────────────────────────────────

def fetch_crypto_categories() -> dict:
    """Fetch category-level data from CoinGecko (no API key needed)."""
    print("🪙 Fetching crypto category data...")
    try:
        url  = "https://api.coingecko.com/api/v3/coins/categories"
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        all_cats = resp.json()

        # Build a lookup by id
        cat_lookup = {c["id"]: c for c in all_cats}

        result = {}
        for name, cg_id in CRYPTO_CATEGORIES.items():
            cat = cat_lookup.get(cg_id)
            if not cat:
                result[name] = {"error": "category not found"}
                continue
            result[name] = {
                "market_cap_usd":     cat.get("market_cap"),
                "volume_24h_usd":     cat.get("volume_24h"),
                "change_1h_%":        cat.get("market_cap_change_1h"),
                "change_24h_%":       cat.get("market_cap_change_24h"),
                "change_7d_%":        cat.get("market_cap_change_7d"),
                "top_3_coins":        cat.get("top_3_coins_id", []),
            }
        return result
    except Exception as e:
        print(f"⚠️ CoinGecko categories error: {e}")
        return {}


def fetch_crypto_top100() -> list:
    """Fetch top 100 coins by market cap with 7d performance."""
    print("🪙 Fetching crypto top 100...")
    try:
        url    = "https://api.coingecko.com/api/v3/coins/markets"
        params = {
            "vs_currency":          "usd",
            "order":                "market_cap_desc",
            "per_page":             100,
            "page":                 1,
            "price_change_percentage": "24h,7d,30d",
        }
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        coins = resp.json()

        # Keep only relevant fields
        result = []
        for c in coins:
            result.append({
                "rank":         c.get("market_cap_rank"),
                "symbol":       c.get("symbol", "").upper(),
                "name":         c.get("name"),
                "price":        c.get("current_price"),
                "change_24h_%": round(c.get("price_change_percentage_24h") or 0, 2),
                "change_7d_%":  round(c.get("price_change_percentage_7d_in_currency") or 0, 2),
                "change_30d_%": round(c.get("price_change_percentage_30d_in_currency") or 0, 2),
                "volume_24h":   c.get("total_volume"),
                "market_cap":   c.get("market_cap"),
                "ath_ratio_%":  round((c.get("current_price", 0) / c.get("ath", 1)) * 100, 1) if c.get("ath") else None,
            })
        return result
    except Exception as e:
        print(f"⚠️ CoinGecko top100 error: {e}")
        return []


def identify_crypto_movers(top100: list) -> dict:
    """Extract notable movers from top 100 for the prompt."""
    if not top100:
        return {}

    # Top gainers 7d
    gainers_7d = sorted(
        [c for c in top100 if c.get("change_7d_%") is not None],
        key=lambda x: x["change_7d_%"], reverse=True
    )[:10]

    # Top losers 7d
    losers_7d = sorted(
        [c for c in top100 if c.get("change_7d_%") is not None],
        key=lambda x: x["change_7d_%"]
    )[:5]

    # Near ATH (potential breakout)
    near_ath = [c for c in top100 if c.get("ath_ratio_%", 0) >= 85]

    return {
        "top_gainers_7d": gainers_7d,
        "top_losers_7d":  losers_7d,
        "near_ath":       near_ath,
    }


# ── MEMORY ───────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE, "r") as f:
            return json.load(f)
    except Exception:
        return {}


def extract_signals(market_data: dict) -> dict:
    signals = {}

    # Stock sectors
    for sector, data in market_data.get("stock_sectors", {}).items():
        if not data or "error" in data:
            continue
        signals[f"stock_{sector}"] = {
            "change_1w_%":   data.get("change_1w_%"),
            "change_1m_%":   data.get("change_1m_%"),
            "volume_ratio":  data.get("volume_ratio"),
            "near_52w_high": data.get("near_52w_high"),
        }

    # Crypto categories
    for cat, data in market_data.get("crypto_categories", {}).items():
        if not data or "error" in data:
            continue
        signals[f"crypto_{cat}"] = {
            "change_7d_%":  data.get("change_7d_%"),
            "change_24h_%": data.get("change_24h_%"),
        }

    # Notable stock tickers
    notable_tickers = {}
    for ticker, data in market_data.get("stock_tickers", {}).items():
        if not data or "error" in data:
            continue
        if abs(data.get("change_1w_%", 0)) >= 5 or data.get("volume_ratio", 0) >= 1.5:
            notable_tickers[ticker] = {
                "change_1w_%":   data.get("change_1w_%"),
                "change_1m_%":   data.get("change_1m_%"),
                "volume_ratio":  data.get("volume_ratio"),
                "near_52w_high": data.get("near_52w_high"),
            }

    # Notable crypto movers (top 10 gainers 7d)
    for coin in market_data.get("crypto_movers", {}).get("top_gainers_7d", [])[:5]:
        notable_tickers[coin["symbol"]] = {
            "change_7d_%":  coin.get("change_7d_%"),
            "change_30d_%": coin.get("change_30d_%"),
            "rank":         coin.get("rank"),
        }

    return {"sectors": signals, "notable_tickers": notable_tickers}


def save_memory(market_data: dict, analysis: str):
    memory = load_memory()
    today  = market_data["date"]

    memory[today] = {
        "signals":       extract_signals(market_data),
        "brief_summary": analysis[:600],
    }

    cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).strftime("%Y-%m-%d")
    memory = {k: v for k, v in memory.items() if k >= cutoff}
    memory = dict(sorted(memory.items()))

    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

    print(f"💾 Memory saved ({len(memory)} entries)")


def build_memory_context(memory: dict) -> str:
    if not memory:
        return "No historical data yet — this is the first run."

    lines = ["HISTORICAL SIGNAL TRACKER:\n"]

    all_sectors = set()
    for day_data in memory.values():
        all_sectors.update(day_data.get("signals", {}).get("sectors", {}).keys())

    lines.append("── Sector/Category momentum over time (change_1w_% or change_7d_%) ──")
    for sector in sorted(all_sectors):
        row = f"{sector:35s}"
        for day, day_data in sorted(memory.items())[-10:]:
            val = day_data.get("signals", {}).get("sectors", {}).get(sector, {})
            chg = val.get("change_1w_%") or val.get("change_7d_%")
            row += f"  {day[5:]}:{chg:+.1f}%" if chg is not None else "         —"
        lines.append(row)

    lines.append("\n── Notable assets on radar ──")
    ticker_history = {}
    for day, day_data in sorted(memory.items()):
        for ticker, tdata in day_data.get("signals", {}).get("notable_tickers", {}).items():
            if ticker not in ticker_history:
                ticker_history[ticker] = []
            ticker_history[ticker].append({"date": day, **tdata})

    for ticker, appearances in sorted(ticker_history.items()):
        latest = appearances[-1]
        chg = latest.get("change_1w_%") or latest.get("change_7d_%") or 0
        lines.append(
            f"{ticker}: on radar {len(appearances)}x | "
            f"latest chg={chg:+.1f}% "
            f"vol={latest.get('volume_ratio', '—')}"
        )

    lines.append("\n── Recent summaries ──")
    for day, day_data in sorted(memory.items())[-5:]:
        summary = day_data.get("brief_summary", "")[:200]
        lines.append(f"\n{day}:\n{summary}...")

    return "\n".join(lines)


# ── AI ANALYSIS ──────────────────────────────────────────────────────────────

def analyze_with_claude(market_data: dict, memory: dict) -> str:
    print("🤖 Running Claude analysis...")

    client         = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    memory_context = build_memory_context(memory)

    prompt = f"""
You are a sharp financial analyst specializing in identifying EARLY-STAGE sector trends on US stock markets AND crypto markets —
the kind of trends that were visible in semiconductors in 2019-2020 BEFORE the mainstream caught on.
You analyze WEEKLY timeframes and multi-month structural trends — macro rotations, sector accumulation phases,
early institutional positioning. Ignore daily noise. A signal is only relevant if it holds across multiple weeks.

Today is {market_data['date']}.

═══════════════════════════════════════════
STOCK MARKET DATA
═══════════════════════════════════════════

SECTOR ETFs:
{json.dumps(market_data['stock_sectors'], indent=2)}

INDIVIDUAL TICKERS:
{json.dumps(market_data['stock_tickers'], indent=2)}

═══════════════════════════════════════════
CRYPTO MARKET DATA
═══════════════════════════════════════════

CRYPTO SECTORS (by category):
{json.dumps(market_data['crypto_categories'], indent=2)}

TOP 100 NOTABLE MOVERS:
{json.dumps(market_data['crypto_movers'], indent=2)}

═══════════════════════════════════════════
HISTORICAL MEMORY
═══════════════════════════════════════════
{memory_context}

═══════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════

Produce a weekly briefing in two parts: STOCKS and CRYPTO.
Use historical memory to track signal persistence — your edge.

── STOCKS ──

1. 🔥 TOP EMERGING STOCK TREND (1-2 sectors)
   - Signal + duration (reference memory)
   - Macro catalyst
   - Tickers to watch
   - Risk: LOW / MEDIUM / HIGH

2. 📊 STOCK SECTOR SCORECARD
   - All sectors: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   - NEW / CONFIRMED / FADING tag

── CRYPTO ──

3. 🔥 TOP EMERGING CRYPTO TREND (1-2 categories)
   - Which category is rotating? (L1, meme, AI, RWA, etc.)
   - Signal + duration from memory
   - Top coins to watch in that category (from top 100)
   - Risk: LOW / MEDIUM / HIGH

4. 📊 CRYPTO CATEGORY SCORECARD
   - All categories: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻

── CROSS-MARKET ──

5. 🔁 SIGNAL UPDATES (memory follow-ups)
   - Resolve flagged signals from previous weeks
   - "X weeks ago we flagged Y — confirmed / faded / still building"

6. 💡 EARLY RADAR (cross-market)
   - 1-2 themes too early to call — stocks OR crypto
   - How many weeks on watch, what confirmation to look for

7. ⚠️ KEY RISKS

Punchy. Weekly investor timeframe. Use emojis and clear sections. No disclaimers.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2200,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ── EMAIL DELIVERY ───────────────────────────────────────────────────────────

def send_email(analysis: str, run_date: str):
    print("📧 Sending email...")

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Weekly Market Brief — {run_date}"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)

    text_part = MIMEText(analysis, "plain")
    html_body = analysis.replace("\n", "<br>")
    html = f"""
    <html><body style="font-family: 'Georgia', serif; max-width: 680px; margin: auto;
                        background: #0d0d0d; color: #e8e8e8; padding: 32px;">
      <div style="border-left: 4px solid #00ff88; padding-left: 20px; margin-bottom: 24px;">
        <h1 style="color: #00ff88; font-size: 22px; margin: 0;">📈 Weekly Market Brief</h1>
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
    print(f"📚 Loaded {len(memory)} weeks of memory")

    # Fetch all data
    stock_data        = fetch_all_stock_data()
    crypto_categories = fetch_crypto_categories()
    time.sleep(2)  # CoinGecko rate limit (free tier: 10-30 req/min)
    crypto_top100     = fetch_crypto_top100()
    crypto_movers     = identify_crypto_movers(crypto_top100)

    market_data = {
        "date":             datetime.now().strftime("%Y-%m-%d"),
        "stock_sectors":    stock_data["sectors"],
        "stock_tickers":    stock_data["tickers"],
        "crypto_categories": crypto_categories,
        "crypto_movers":    crypto_movers,
    }

    analysis = analyze_with_claude(market_data, memory)

    save_memory(market_data, analysis)
    send_email(analysis, market_data["date"])

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()