"""
📈 Weekly Stock Trend Analyst Bot
Detects early-stage sector trends on US stock markets.
Includes persistent memory + dynamic ticker discovery.
"""

import os
import json
import requests
import time
from datetime import datetime, timedelta
from bs4 import BeautifulSoup
import anthropic
import yfinance as yf
from bot_utils import markdown_to_html, send_email as _send_email

# ── CONFIG ───────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]
SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD  = os.environ["SENDER_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

MEMORY_FILE         = "memory_stocks.json"
DISCOVERY_FILE      = "discovered_tickers.json"
MEMORY_DAYS         = 180
DISCOVERY_MAX_DAYS  = 90

# ── SECTOR ETFs ───────────────────────────────────────────────────────────────
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


# ── MACRO INDICATORS ─────────────────────────────────────────────────────────
MACRO_TICKERS = {
    "Gold":         "GC=F",
    "Silver":       "SI=F",
    "Oil (WTI)":    "CL=F",
    "EUR/USD":      "EURUSD=X",
    "VIX":          "^VIX",
}

# ── THEMATIC VALUE CHAINS ─────────────────────────────────────────────────────
THEMATIC_CHAINS = {
    "AI Infrastructure": {
        "Chips & Silicon":      ["NVDA", "AMD", "INTC", "AVGO", "MRVL", "QCOM"],
        "Wafer Foundry":        ["TSM", "TSEM", "GFS", "UMC"],
        "AI Servers & Systems": ["SMCI", "DELL", "HPE"],
        "Hyperscalers":         ["MSFT", "GOOGL", "AMZN", "META", "ORCL"],
        "AI Software":          ["PLTR", "AI", "SOUN", "BBAI"],
        "Power & Cooling":      ["CEG", "VST", "ETN", "VRT"],
    },
    "Optical Communications": {
        "Photonic IC & Design":    ["NVDA", "MRVL", "AVGO", "LITE", "COHR"],
        "Laser & Light Source":    ["LITE", "COHR", "AVGO", "AAOI"],
        "Optical Module Assembly": ["COHR", "LITE", "CSCO", "MRVL", "NOK"],
        "Optical Fiber":           ["GLW", "CIEN", "FN"],
        "Packaging & Testing":     ["AMKR", "KEYS", "TER", "FORM"],
        "Materials":               ["AXTI", "MKSI"],
    },
    "Robotics & Automation": {
        "Industrial Robots":    ["ROK", "EMR", "ABBNY"],
        "Humanoid & AI Robots": ["TSLA", "NVDA"],
        "Surgical Robots":      ["ISRG"],
        "Drone & Autonomous":   ["AVAV", "JOBY", "ACHR"],
        "Robot Vision":         ["CGNX", "AZTA"],
    },
    "Quantum Computing": {
        "Pure Play Quantum":   ["IONQ", "RGTI", "QBTS", "QUBT"],
        "Quantum via BigTech": ["IBM", "GOOGL", "MSFT"],
    },
    "Space & Defense AI": {
        "Satellites & Launch": ["RKLB", "ASTS", "SPIR"],
        "Defense Tech & AI":   ["PLTR", "LDOS", "BAH", "SAIC"],
        "Drones & UAV":        ["AVAV", "KTOS"],
        "Space Primes":        ["LMT", "RTX", "NOC", "GD"],
    },
    "Energy for AI": {
        "Nuclear":           ["CEG", "VST", "NNE", "SMR", "OKLO"],
        "Grid & Power Mgmt": ["ETN", "GNRC", "NEE"],
        "DC Power":          ["VRT", "BE"],
    },
}

WATCHLIST = list(set(
    ticker
    for chain in THEMATIC_CHAINS.values()
    for tickers in chain.values()
    for ticker in tickers
))


# ── FINVIZ HELPERS ────────────────────────────────────────────────────────────
FINVIZ_CACHE = {}

def fetch_forward_pe(ticker: str):
    if ticker in FINVIZ_CACHE:
        return FINVIZ_CACHE[ticker]
    try:
        url  = f"https://finviz.com/quote.ashx?t={ticker}"
        hdrs = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(url, headers=hdrs, timeout=10)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        tds  = soup.find_all("td")
        for i, td in enumerate(tds):
            if td.text.strip() in ("Fwd P/E", "P/E") and i + 1 < len(tds):
                val = tds[i + 1].text.strip()
                result = float(val) if val not in ("-", "N/A", "") else None
                FINVIZ_CACHE[ticker] = result
                return result
    except Exception:
        pass
    FINVIZ_CACHE[ticker] = None
    return None


def fetch_finviz_screener() -> list:
    """Scrape Finviz screener: RSI 45-65, above MA50, mid/large cap, vol spike."""
    print("🔍 Finviz screener discovery...")
    discovered = []
    try:
        url  = (
            "https://finviz.com/screener.ashx?v=111"
            "&f=cap_midlarge,rsi_nm,ta_sma50_pa,sh_avgvol_o500,sh_price_o5"
            "&ft=4&o=-volume"
        )
        hdrs = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(url, headers=hdrs, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        for link in soup.select("a.screener-link-primary")[:30]:
            t = link.text.strip()
            if t and t.isalpha() and len(t) <= 5:
                discovered.append({"ticker": t, "source": "finviz_screener",
                                   "note": "RSI 45-65, above MA50, vol spike"})
        print(f"  → {len(discovered)} tickers found")
        time.sleep(1)
    except Exception as e:
        print(f"  ⚠️ Finviz screener error: {e}")
    return discovered


def fetch_yahoo_trending() -> list:
    """Fetch Yahoo Finance trending tickers."""
    print("🔍 Yahoo trending discovery...")
    discovered = []
    try:
        url  = "https://finance.yahoo.com/trending-tickers/"
        hdrs = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(url, headers=hdrs, timeout=10)
        soup = BeautifulSoup(resp.text, "html.parser")
        found = set()
        for link in soup.select("a[href*='/quote/']"):
            s = link.text.strip()
            if s and 1 < len(s) <= 5 and s.isupper() and s.isalpha():
                found.add(s)
        known = set(WATCHLIST + list(SECTOR_ETFS.values()))
        new   = [t for t in found if t not in known][:15]
        for t in new:
            discovered.append({"ticker": t, "source": "yahoo_trending",
                               "note": "trending on Yahoo Finance"})
        print(f"  → {len(discovered)} new tickers found")
    except Exception as e:
        print(f"  ⚠️ Yahoo trending error: {e}")
    return discovered


def fetch_discovery_tickers(screener_tickers: list) -> dict:
    """Fetch full data for newly discovered tickers not already in watchlist."""
    known = set(WATCHLIST)
    new   = list(dict.fromkeys(
        t["ticker"] for t in screener_tickers if t["ticker"] not in known
    ))[:20]
    if not new:
        return {}
    print(f"📡 Fetching {len(new)} discovered tickers...")
    results = {}
    for ticker in new:
        data = fetch_ticker_data(ticker)
        if data and "error" not in data:
            results[ticker] = data
    return results


# ── RSI + PRICE DATA ──────────────────────────────────────────────────────────

def compute_rsi(series, period=14):
    try:
        delta = series.diff()
        gain  = delta.clip(lower=0).rolling(period).mean()
        loss  = (-delta.clip(upper=0)).rolling(period).mean()
        rs    = gain / loss
        rsi   = 100 - (100 / (1 + rs))
        val   = rsi.iloc[-1]
        return round(float(val), 1) if val == val else None  # NaN check
    except Exception:
        return None


def fetch_ticker_data(ticker: str, days: int = 30) -> dict:
    try:
        tk     = yf.Ticker(ticker)
        hist_d = tk.history(period=f"{days}d")
        if hist_d.empty:
            return {}
        hist_w = tk.history(period="1y", interval="1wk")

        current    = hist_d["Close"].iloc[-1]
        prev_week  = hist_d["Close"].iloc[-5] if len(hist_d) >= 5 else hist_d["Close"].iloc[0]
        prev_month = hist_d["Close"].iloc[0]
        avg_vol    = hist_d["Volume"].mean()
        last_vol   = hist_d["Volume"].iloc[-1]

        rsi_weekly = compute_rsi(hist_w["Close"], 14) if len(hist_w) >= 15 else None

        ma50_w = ma200_w = cross_signal = None
        if len(hist_w) >= 10:
            closes_w = hist_w["Close"]
            if len(closes_w) >= 50:
                ma50_w = round(float(closes_w.rolling(50).mean().iloc[-1]), 2)
            if len(closes_w) >= 200:
                ma200_w = round(float(closes_w.rolling(200).mean().iloc[-1]), 2)
            if ma50_w and ma200_w:
                p50  = float(closes_w.rolling(50).mean().iloc[-2]) if len(closes_w) >= 51 else None
                p200 = float(closes_w.rolling(200).mean().iloc[-2]) if len(closes_w) >= 201 else None
                if p50 and p200:
                    if p50 < p200 and ma50_w > ma200_w:
                        cross_signal = "GOLDEN_CROSS"
                    elif p50 > p200 and ma50_w < ma200_w:
                        cross_signal = "DEATH_CROSS"
                    elif ma50_w > ma200_w:
                        cross_signal = "ABOVE_200"
                    else:
                        cross_signal = "BELOW_200"

        rsi_zone = None
        if rsi_weekly is not None:
            if rsi_weekly >= 75:   rsi_zone = "OVERBOUGHT"
            elif rsi_weekly >= 60: rsi_zone = "EXTENDED"
            elif rsi_weekly <= 30: rsi_zone = "OVERSOLD"
            elif rsi_weekly <= 45: rsi_zone = "NEUTRAL_LOW"
            else:                  rsi_zone = "NEUTRAL"

        fwd_pe = fetch_forward_pe(ticker)
        time.sleep(0.5)

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
            "rsi_zone":      rsi_zone,
            "ma50_weekly":   ma50_w,
            "ma200_weekly":  ma200_w,
            "ma_cross":      cross_signal,
        }
    except Exception as e:
        return {"ticker": ticker, "error": str(e)}


# ── FETCH ALL ─────────────────────────────────────────────────────────────────


def fetch_macro_data() -> dict:
    """Fetch commodities + forex weekly data."""
    print("🌍 Fetching macro indicators...")
    result = {}
    for name, ticker in MACRO_TICKERS.items():
        try:
            tk     = yf.Ticker(ticker)
            hist   = tk.history(period="60d")
            if hist.empty:
                continue
            current    = hist["Close"].iloc[-1]
            prev_week  = hist["Close"].iloc[-5] if len(hist) >= 5 else hist["Close"].iloc[0]
            prev_month = hist["Close"].iloc[0]
            hist_w     = tk.history(period="1y", interval="1wk")
            rsi_w      = compute_rsi(hist_w["Close"], 14) if len(hist_w) >= 15 else None

            # Trend based on MA
            ma20 = float(hist["Close"].rolling(20).mean().iloc[-1]) if len(hist) >= 20 else None
            trend = None
            if ma20:
                if current > ma20 * 1.02:  trend = "UPTREND"
                elif current < ma20 * 0.98: trend = "DOWNTREND"
                else:                       trend = "SIDEWAYS"

            result[name] = {
                "price":       round(float(current), 4),
                "change_1w_%": round(float((current - prev_week) / prev_week * 100), 2),
                "change_1m_%": round(float((current - prev_month) / prev_month * 100), 2),
                "rsi_weekly":  rsi_w,
                "trend":       trend,
            }
        except Exception as e:
            result[name] = {"error": str(e)}
    return result

def fetch_all_data() -> dict:
    print(f"📡 Fetching {len(WATCHLIST)} watchlist tickers across {len(THEMATIC_CHAINS)} chains...")
    sectors = {name: fetch_ticker_data(etf) for name, etf in SECTOR_ETFS.items()}
    tickers = {t: fetch_ticker_data(t) for t in WATCHLIST}

    chains = {}
    for chain_name, sub_chains in THEMATIC_CHAINS.items():
        chains[chain_name] = {}
        for sub_name, ticker_list in sub_chains.items():
            chains[chain_name][sub_name] = {
                t: tickers[t] for t in ticker_list if tickers.get(t)
            }

    screener_raw    = fetch_finviz_screener()
    trending_raw    = fetch_yahoo_trending()
    all_discovered  = screener_raw + trending_raw
    discovered_data = fetch_discovery_tickers(all_discovered)
    for item in all_discovered:
        t = item["ticker"]
        if t in discovered_data:
            discovered_data[t]["discovery_source"] = item["source"]
            discovered_data[t]["discovery_note"]   = item["note"]

    discovery_store   = load_discovered()
    discovery_context = build_discovery_context(discovery_store)

    macro = fetch_macro_data()

    return {
        "sectors":           sectors,
        "tickers":           tickers,
        "chains":            chains,
        "discovered":        discovered_data,
        "discovery_context": discovery_context,
        "macro":             macro,
        "date":              datetime.now().strftime("%Y-%m-%d"),
    }


# ── MEMORY ────────────────────────────────────────────────────────────────────

def load_memory() -> dict:
    if not os.path.exists(MEMORY_FILE):
        return {}
    try:
        with open(MEMORY_FILE) as f:
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
    notable = {}
    for ticker, data in market_data["tickers"].items():
        if not data or "error" in data:
            continue
        if abs(data.get("change_1w_%", 0)) >= 5 or data.get("volume_ratio", 0) >= 1.5:
            notable[ticker] = {
                "change_1w_%":   data.get("change_1w_%"),
                "change_1m_%":   data.get("change_1m_%"),
                "volume_ratio":  data.get("volume_ratio"),
                "near_52w_high": data.get("near_52w_high"),
            }
    return {"sectors": signals, "notable_tickers": notable}


def save_memory(market_data: dict, analysis: str):
    memory = load_memory()
    today  = market_data["date"]
    memory[today] = {
        "signals":       extract_signals(market_data),
        "brief_summary": analysis[:600],
    }
    cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).strftime("%Y-%m-%d")
    memory = dict(sorted({k: v for k, v in memory.items() if k >= cutoff}.items()))
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)
    print(f"💾 Memory saved ({len(memory)} entries)")


def build_memory_context(memory: dict) -> str:
    if not memory:
        return "No historical data yet — this is the first run."
    lines = ["HISTORICAL SIGNAL TRACKER (last 6 months):"]
    all_sectors = set()
    for d in memory.values():
        all_sectors.update(d.get("signals", {}).get("sectors", {}).keys())
    lines.append("── Sector momentum over time (change_1w_%) ──")
    for sector in sorted(all_sectors):
        row = f"{sector:30s}"
        for day, dd in sorted(memory.items())[-10:]:
            val = dd.get("signals", {}).get("sectors", {}).get(sector, {}).get("change_1w_%")
            row += f"  {day[5:]}:{val:+.1f}%" if val is not None else "         —"
        lines.append(row)
    lines.append("\n── Notable ticker appearances ──")
    ticker_history = {}
    for day, dd in sorted(memory.items()):
        for ticker, tdata in dd.get("signals", {}).get("notable_tickers", {}).items():
            ticker_history.setdefault(ticker, []).append({"date": day, **tdata})
    for ticker, appearances in sorted(ticker_history.items()):
        latest = appearances[-1]
        lines.append(
            f"{ticker}: on radar {len(appearances)}x | "
            f"latest 1w={latest.get('change_1w_%', 0):+.1f}% "
            f"vol={latest.get('volume_ratio', 0):.2f}x "
            f"near_52wh={latest.get('near_52w_high')}"
        )
    lines.append("\n── Recent weekly summaries ──")
    for day, dd in sorted(memory.items())[-5:]:
        lines.append(f"\n{day}:\n{dd.get('brief_summary', '')[:200]}...")
    return "\n".join(lines)


# ── DISCOVERY PERSISTENCE ─────────────────────────────────────────────────────

def load_discovered() -> dict:
    if not os.path.exists(DISCOVERY_FILE):
        return {}
    try:
        with open(DISCOVERY_FILE) as f:
            return json.load(f)
    except Exception:
        return {}


def save_discovered(current_discovered: dict, market_data: dict):
    store  = load_discovered()
    today  = market_data["date"]
    known  = set(WATCHLIST)
    cutoff = (datetime.now() - timedelta(days=DISCOVERY_MAX_DAYS)).strftime("%Y-%m-%d")

    for ticker, data in current_discovered.items():
        if ticker in known:
            continue
        rsi = data.get("rsi_weekly")
        if ticker not in store:
            store[ticker] = {
                "first_seen":       today,
                "last_seen":        today,
                "times_appeared":   1,
                "discovery_source": data.get("discovery_source", "unknown"),
                "rsi_history":      [rsi] if rsi else [],
                "signal_history":   [data.get("rsi_zone")] if data.get("rsi_zone") else [],
                "last_data":        {k: v for k, v in data.items()
                                     if k not in ("discovery_source", "discovery_note")},
            }
        else:
            store[ticker]["last_seen"]      = today
            store[ticker]["times_appeared"] += 1
            store[ticker]["last_data"]      = {k: v for k, v in data.items()
                                                if k not in ("discovery_source", "discovery_note")}
            if rsi:
                store[ticker]["rsi_history"] = (store[ticker]["rsi_history"] + [rsi])[-12:]
            if data.get("rsi_zone"):
                store[ticker]["signal_history"] = (store[ticker]["signal_history"] + [data["rsi_zone"]])[-12:]

    store = dict(sorted({k: v for k, v in store.items() if v.get("last_seen", "") >= cutoff}.items()))
    with open(DISCOVERY_FILE, "w") as f:
        json.dump(store, f, indent=2)
    recurring = {k: v for k, v in store.items() if v["times_appeared"] >= 2}
    print(f"💾 Discovery: {len(store)} tracked, {len(recurring)} recurring")


def build_discovery_context(store: dict) -> str:
    if not store:
        return "No previously discovered tickers yet."
    lines = ["PREVIOUSLY DISCOVERED TICKERS (persistent watchlist):"]
    lines.append(f"{'Ticker':<8} {'First':>10} {'Seen':>5}x  {'Last RSI':>8}  {'Zone':<14}  Source")
    lines.append("-" * 70)
    for ticker, info in sorted(store.items(), key=lambda x: -x[1]["times_appeared"]):
        rsi_hist  = info.get("rsi_history", [])
        last_rsi  = f"{rsi_hist[-1]:.0f}" if rsi_hist else "-"
        rsi_trend = ""
        if len(rsi_hist) >= 2:
            diff = rsi_hist[-1] - rsi_hist[-2]
            rsi_trend = f"+{diff:.0f}" if diff > 0 else f"{diff:.0f}"
        sig    = (info.get("signal_history") or ["-"])[-1]
        source = info.get("discovery_source", "-")
        lines.append(
            f"{ticker:<8} {info['first_seen']:>10} {info['times_appeared']:>5}x  "
            f"RSI {last_rsi:>3}{rsi_trend:<5}  {sig:<14}  {source}"
        )
    recurring = {k: v for k, v in store.items() if v["times_appeared"] >= 3}
    if recurring:
        lines.append(f"\n*** HIGH CONVICTION (3+ appearances): {', '.join(recurring.keys())} ***")
        lines.append("    These have persistently appeared — strong signal, worth deep attention")
    return "\n".join(lines)


# ── CLAUDE ANALYSIS ───────────────────────────────────────────────────────────

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
SECTOR ETFs (macro momentum):
{json.dumps(market_data['sectors'], indent=2)}

THEMATIC VALUE CHAINS (full industry stack):
{json.dumps(market_data['chains'], indent=2)}

🌍 MACRO INDICATORS (commodities, forex, rates):
{json.dumps(market_data.get('macro', {}), indent=2)}

NEWLY DISCOVERED TICKERS (Finviz screener + Yahoo trending, not in watchlist):
{json.dumps(market_data.get('discovered', {}), indent=2)}

DISCOVERY HISTORY (persistent — seen in previous weeks):
{market_data.get('discovery_context', 'No history yet.')}

HISTORICAL MEMORY (past 6 months):
{memory_context}
═══════════════════════════════════════════

YOUR TASK — use ALL data above. Memory and discovery history are your edge.
A signal is stronger when it propagates across multiple chain layers.

1. 🌍 MACRO PULSE (5 lines max)
   - Gold/Silver/Oil: safe haven + inflation signal
   - EUR/USD trend: risk-on vs risk-off, USD strength impact on US equities
   - VIX level + direction: <15 complacent, 15-25 normal, >25 fear, >30 panic
   - One sentence conclusion: macro SUPPORTIVE / NEUTRAL / HEADWIND for equities

2. 🔥 TOP EMERGING TREND (1-2 themes)
   - Which thematic chain is activating? Which sub-layers are moving?
   - Full-chain move vs isolated layer? (full-chain = stronger conviction)
   - Signal duration from memory + macro catalyst
   - 3-5 tickers with: fwd_pe, rsi_weekly, ma_cross + label:
       🚀 EARLY TREND  : RSI 45-60, above MA50, near golden cross → ideal entry
       📈 CONFIRMED    : RSI 60-70, above both MAs, solid momentum
       ⚠️ NEAR TOP     : RSI >70, near 52w high — risk/reward deteriorating
       🔥 EXTENDED RUN : RSI >75, parabolic — late stage, tight stops
       💤 LAGGING      : below MA50, RSI <45 — not yet activated → watch list
   - Risk level: LOW / MEDIUM / HIGH

3. 📊 SECTOR SCORECARD
   - All ETF sectors: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   - Tag: NEW / CONFIRMED (3+ weeks) / FADING

4. 🔗 CHAIN ANALYSIS
   - For each active chain: which sub-layers lead vs lag?
   - Lagging layer in active chain + RSI <50 + ABOVE_200 = highest conviction catch-up
   - Include fwd_pe context: low PE in hot chain = value entry

5. 🔍 NEW ON RADAR (discovery)
   - Flag interesting tickers from NEWLY DISCOVERED + DISCOVERY HISTORY
   - Prioritize tickers appearing 2nd, 3rd time — persistence = conviction
   - HIGH CONVICTION (3+ appearances) get their own paragraph
   - Apply RSI/MA/PE labels, explain which chain they could belong to
   - Max 4-5 tickers

6. 🔁 SIGNAL UPDATES (memory follow-ups)
   - Follow up on signals from previous weeks — confirmed / faded / still building

7. 💡 EARLY RADAR
   - 1-2 chains too early to call — weeks on watch, what would confirm?

7. ⚠️ RISKS THIS WEEK

Punchy. 3-minute weekly read. Emojis, clear sections. No disclaimers.
CRITICAL: Never cut a section mid-sentence. If running long, shorten each section but always complete every section fully.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}]
    )
    return message.content[0].text


# ── EMAIL ─────────────────────────────────────────────────────────────────────

def send_email(analysis: str, run_date: str):
    print("📧 Sending email...")
    _send_email(
        analysis        = analysis,
        run_date        = run_date,
        sender_email    = SENDER_EMAIL,
        sender_password = SENDER_PASSWORD,
        recipients      = RECIPIENT_EMAILS,
        subject         = f"📈 Weekly Stock Brief — {run_date}",
        header_title    = "📈 Weekly Stock Brief",
        header_color    = "#1a6b3c",
        bg_color        = "#faf8f4",
        accent          = "#145c30",
        footer          = "Financial Trend Bot · Not financial advice",
    )


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🚀 Stock Trend Bot starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    memory      = load_memory()
    print(f"📚 Loaded {len(memory)} weeks of memory")

    market_data = fetch_all_data()
    analysis    = analyze_with_claude(market_data, memory)

    save_memory(market_data, analysis)
    save_discovered(market_data.get("discovered", {}), market_data)
    send_email(analysis, market_data["date"])

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()