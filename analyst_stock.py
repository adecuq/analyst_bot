"""
📈 Morning Financial Trend Analyst Bot — STOCKS ONLY
Weekly timeframe analysis of US stock markets.
Includes persistent memory across runs to track evolving signals.
"""

import os
import smtplib
import json
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import datetime, timedelta
import anthropic
import requests
from bs4 import BeautifulSoup
import time
import yfinance as yf

# ── CONFIG ──────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]
SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD  = os.environ["SENDER_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

MEMORY_FILE  = "memory_stocks.json"
MEMORY_DAYS  = 180  # 6 months — weekly timeframe analysis

# ── Sector ETFs ──────────────────────────────────────────────────────────────
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

# ── Thematic Value Chains ────────────────────────────────────────────────────
# Each chain covers the full industry stack.
# Claude analyzes momentum across the entire chain, not just top names.

THEMATIC_CHAINS = {

    "AI Infrastructure": {
        "Chips & Silicon":          ["NVDA", "AMD", "INTC", "AVGO", "MRVL", "QCOM"],
        "Wafer Foundry":            ["TSM", "TSEM", "GFS", "UMC"],
        "AI Servers & Systems":     ["SMCI", "DELL", "HPE"],
        "Hyperscalers":             ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
        "AI Software & Models":     ["PLTR", "AI", "SOUN", "BBAI"],
        "Power & Cooling":          ["CEG", "VST", "ETN", "VRT"],
    },

    "Optical Communications": {
        "Photonic IC & Design":     ["NVDA", "MRVL", "AVGO", "LITE", "COHR"],
        "Laser & Light Source":     ["LITE", "COHR", "AVGO", "AAOI"],
        "Optical Module Assembly":  ["COHR", "LITE", "CSCO", "MRVL", "NOK"],
        "Optical Fiber":            ["GLW", "CIEN", "FN"],
        "Packaging & Testing":      ["AMKR", "KEYS", "TER", "FORM"],
        "Materials":                ["AXTI", "MKSI"],
    },

    "Robotics & Automation": {
        "Industrial Robots":        ["ROK", "EMR", "ABBNY"],
        "Humanoid & AI Robots":     ["TSLA", "NVDA"],
        "Surgical Robots":          ["ISRG"],
        "Drone & Autonomous":       ["AVAV", "JOBY", "ACHR"],
        "Robot Vision & Software":  ["CGNX", "AZTA"],
    },

    "Quantum Computing": {
        "Pure Play Quantum":        ["IONQ", "RGTI", "QBTS", "QUBT"],
        "Quantum via Big Tech":     ["IBM", "GOOGL", "MSFT"],
    },

    "Space & Defense AI": {
        "Satellites & Launch":      ["RKLB", "ASTS", "SPIR"],
        "Defense Tech & AI":        ["PLTR", "LDOS", "BAH", "SAIC"],
        "Drones & UAV":             ["AVAV", "KTOS"],
        "Space Infrastructure":     ["LMT", "RTX", "NOC", "GD"],
    },

    "Energy for AI": {
        "Nuclear":                  ["CEG", "VST", "NNE", "SMR", "OKLO"],
        "Grid & Power Mgmt":        ["ETN", "GNRC", "NEE"],
        "Data Center Power":        ["VRT", "BE"],
    },
}

# Flatten all tickers (deduplicated)
WATCHLIST = list(set(
    ticker
    for chain in THEMATIC_CHAINS.values()
    for tickers in chain.values()
    for ticker in tickers
))


# ── DATA FETCHING ────────────────────────────────────────────────────────────

def compute_rsi(series, period=14) -> float | None:
    """Compute RSI on a price series."""
    try:
        delta  = series.diff()
        gain   = delta.clip(lower=0).rolling(period).mean()
        loss   = (-delta.clip(upper=0)).rolling(period).mean()
        rs     = gain / loss
        rsi    = 100 - (100 / (1 + rs))
        val    = rsi.iloc[-1]
        return round(float(val), 1) if not (val != val) else None  # NaN check
    except Exception:
        return None


def fetch_ticker_data(ticker: str, days: int = 30) -> dict:
    try:
        tk = yf.Ticker(ticker)

        # Daily data for price/volume metrics
        hist_d = tk.history(period=f"{days}d")
        if hist_d.empty:
            return {}

        # Weekly data (1y) for RSI weekly + MA50/200
        hist_w = tk.history(period="1y", interval="1wk")

        current    = hist_d["Close"].iloc[-1]
        prev_week  = hist_d["Close"].iloc[-5] if len(hist_d) >= 5 else hist_d["Close"].iloc[0]
        prev_month = hist_d["Close"].iloc[0]
        avg_vol    = hist_d["Volume"].mean()
        last_vol   = hist_d["Volume"].iloc[-1]

        # ── Weekly RSI (14 periods) ──
        rsi_weekly = compute_rsi(hist_w["Close"], period=14) if len(hist_w) >= 15 else None

        # ── Weekly MA50 / MA200 (approximated on weekly bars) ──
        ma50_w  = None
        ma200_w = None
        cross_signal = None
        if len(hist_w) >= 10:
            closes_w = hist_w["Close"]
            if len(closes_w) >= 50:
                ma50_w  = round(float(closes_w.rolling(50).mean().iloc[-1]), 2)
            if len(closes_w) >= 200:
                ma200_w = round(float(closes_w.rolling(200).mean().iloc[-1]), 2)
            # Golden/Death cross detection on weekly
            if ma50_w and ma200_w:
                prev_ma50  = float(closes_w.rolling(50).mean().iloc[-2]) if len(closes_w) >= 51 else None
                prev_ma200 = float(closes_w.rolling(200).mean().iloc[-2]) if len(closes_w) >= 201 else None
                if prev_ma50 and prev_ma200:
                    if prev_ma50 < prev_ma200 and ma50_w > ma200_w:
                        cross_signal = "GOLDEN_CROSS"
                    elif prev_ma50 > prev_ma200 and ma50_w < ma200_w:
                        cross_signal = "DEATH_CROSS"
                    elif ma50_w > ma200_w:
                        cross_signal = "ABOVE_200"
                    else:
                        cross_signal = "BELOW_200"

        # ── RSI zone interpretation ──
        rsi_zone = None
        if rsi_weekly is not None:
            if rsi_weekly >= 75:
                rsi_zone = "OVERBOUGHT"
            elif rsi_weekly >= 60:
                rsi_zone = "EXTENDED"
            elif rsi_weekly <= 30:
                rsi_zone = "OVERSOLD"
            elif rsi_weekly <= 45:
                rsi_zone = "NEUTRAL_LOW"
            else:
                rsi_zone = "NEUTRAL"

        fwd_pe = fetch_forward_pe(ticker)
        time.sleep(0.5)  # polite Finviz rate limit

        return {
            "ticker":        ticker,
            "price":         round(float(current), 2),
            "change_1w_%":   round(float((current - prev_week) / prev_week * 100), 2),
            "change_1m_%":   round(float((current - prev_month) / prev_month * 100), 2),
            "volume_ratio":  round(float(last_vol / avg_vol), 2),
            "52w_high":      round(float(hist_d["Close"].max()), 2),
            "near_52w_high": bool(current >= hist_d["Close"].max() * 0.95),
            "fwd_pe":        fwd_pe,
            "rsi_weekly":    rsi_weekly,
            "rsi_zone":      rsi_zone,      # OVERBOUGHT / EXTENDED / NEUTRAL / OVERSOLD
            "ma50_weekly":   ma50_w,
            "ma200_weekly":  ma200_w,
            "ma_cross":      cross_signal,  # GOLDEN_CROSS / DEATH_CROSS / ABOVE_200 / BELOW_200
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


def fetch_all_data() -> dict:
    print(f"📡 Fetching stock market data ({len(WATCHLIST)} tickers across {len(THEMATIC_CHAINS)} chains)...")
    sectors = {name: fetch_ticker_data(etf) for name, etf in SECTOR_ETFS.items()}
    tickers = {t: fetch_ticker_data(t) for t in WATCHLIST}

    # Build chain-structured data for the prompt
    chains = {}
    for chain_name, sub_chains in THEMATIC_CHAINS.items():
        chains[chain_name] = {}
        for sub_name, ticker_list in sub_chains.items():
            chains[chain_name][sub_name] = {
                t: tickers.get(t, {}) for t in ticker_list if tickers.get(t)
            }

    return {
        "sectors": sectors,
        "tickers": tickers,
        "chains":  chains,
        "date":    datetime.now().strftime("%Y-%m-%d"),
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
        if abs(data.get("change_1w_%", 0)) >= 5 or data.get("volume_ratio", 0) >= 1.5:
            notable_tickers[ticker] = {
                "change_1w_%":   data.get("change_1w_%"),
                "change_1m_%":   data.get("change_1m_%"),
                "volume_ratio":  data.get("volume_ratio"),
                "near_52w_high": data.get("near_52w_high"),
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

    lines = ["HISTORICAL SIGNAL TRACKER (last 6 months):\n"]

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
            f"{ticker}: on radar {len(appearances)}x | "
            f"latest 1w={latest.get('change_1w_%', 0):+.1f}% "
            f"vol={latest.get('volume_ratio', 0):.2f}x "
            f"near_52wh={latest.get('near_52w_high')}"
        )

    lines.append("\n── Recent weekly summaries ──")
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
You are a sharp financial analyst specializing in identifying EARLY-STAGE sector trends on US markets —
the kind of trends that were visible in semiconductors in 2019-2020 BEFORE the mainstream caught on.
You analyze WEEKLY timeframes and multi-month structural trends — macro rotations, sector accumulation phases,
early institutional positioning. Ignore daily noise. A signal is only relevant if it holds across multiple weeks.

Today is {market_data['date']}.

═══════════════════════════════════════════
FRESH MARKET DATA (today)
═══════════════════════════════════════════

SECTOR ETFs (macro momentum):
{json.dumps(market_data['sectors'], indent=2)}

THEMATIC VALUE CHAINS (full industry stack):
{json.dumps(market_data['chains'], indent=2)}

═══════════════════════════════════════════
HISTORICAL MEMORY (past 6 months)
═══════════════════════════════════════════
{memory_context}

═══════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════

Use BOTH today's data AND historical memory. The memory is your edge — use it to track signal persistence.
The THEMATIC VALUE CHAINS are key — analyze momentum not just at sector level but along the full industry stack.
A signal is stronger when it propagates across multiple layers of a chain (e.g. materials → components → systems → hyperscalers).

1. 🔥 TOP EMERGING TREND (1-2 themes)
   - Which thematic chain is activating? Which sub-layers are moving?
   - Is it a full-chain move or isolated to one layer? (full-chain = stronger signal)
   - Signal duration from memory + macro catalyst
   - 3-5 specific tickers — include for each: fwd_pe, rsi_weekly, ma_cross
   - Classify each ticker with one of these labels based on technicals:
       🚀 EARLY TREND  : RSI 45-60, price above MA50, MA50 crossing or recently above MA200 → ideal entry zone
       📈 CONFIRMED    : RSI 60-70, above both MAs, momentum solid but not stretched
       ⚠️ NEAR TOP     : RSI >70, overbought, near 52w high — risk/reward deteriorating
       🔥 EXTENDED RUN : RSI >75, parabolic — momentum still there but late-stage, tight stops needed
       💤 LAGGING      : below MA50, RSI <45 — chain active but this layer hasn't moved yet → watch list
   - Risk level: LOW / MEDIUM / HIGH

2. 📊 SECTOR SCORECARD
   - All ETF sectors: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   - Tag each: NEW / CONFIRMED (3+ weeks) / FADING

3. 🔗 CHAIN ANALYSIS (the edge)
   - For each active thematic chain, which sub-layers are moving vs lagging?
   - Lagging sub-layers in an active chain = potential catch-up plays
   - Example: "Optical Comms chain active — Module Assembly (COHR +18%, Fwd PE 22x) leading, Fiber (GLW) lagging → GLW catch-up candidate"
   - Flag valuation outliers: very high fwd_pe in a hot chain = momentum play; low fwd_pe = value entry
   - Use rsi_zone and ma_cross to classify each sub-layer:
       LAGGING sub-layer with RSI <50 + ABOVE_200 = highest conviction catch-up
       LAGGING sub-layer with RSI <50 + BELOW_200 = wait for confirmation first

4. 🔁 SIGNAL UPDATES (memory follow-ups)
   - Follow up on signals from previous weeks
   - "X weeks ago flagged Y — confirmed / faded / still building"

5. 💡 EARLY RADAR
   - 1-2 chains or sub-layers too early to call
   - How many weeks on watch, what would confirm?

6. ⚠️ RISKS THIS WEEK

Punchy. Weekly investor read — 3 minutes max. Use emojis and clear sections. No disclaimers.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2500,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(analysis: str, run_date: str):
    print("📧 Sending email...")

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"📈 Weekly Stock Brief — {run_date}"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)

    text_part = MIMEText(analysis, "plain")
    html_body = analysis.replace("\n", "<br>")
    html = f"""
    <html><body style="font-family: 'Georgia', serif; max-width: 680px; margin: auto;
                        background: #0d0d0d; color: #e8e8e8; padding: 32px;">
      <div style="border-left: 4px solid #00ff88; padding-left: 20px; margin-bottom: 24px;">
        <h1 style="color: #00ff88; font-size: 22px; margin: 0;">📈 Weekly Stock Brief</h1>
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
    print(f"\n🚀 Stock Trend Bot starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    memory      = load_memory()
    print(f"📚 Loaded {len(memory)} weeks of memory")

    market_data = fetch_all_data()
    analysis    = analyze_with_claude(market_data, memory)

    save_memory(market_data, analysis)
    send_email(analysis, market_data["date"])

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()