"""
🪙 Crypto Trend Analyst Bot — TOP 100 + SECTORS
Run manually when you want a crypto market analysis.
Includes persistent memory to track evolving signals.
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

# ── CONFIG ──────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]
SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD  = os.environ["SENDER_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

MEMORY_FILE  = "memory_crypto.json"
MEMORY_DAYS  = 180  # 6 months

# ── CoinGecko category IDs ───────────────────────────────────────────────────
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


# ── DATA FETCHING ────────────────────────────────────────────────────────────

def fetch_crypto_categories() -> dict:
    print("📡 Fetching crypto category data...")
    try:
        resp = requests.get("https://api.coingecko.com/api/v3/coins/categories", timeout=15)
        resp.raise_for_status()
        cat_lookup = {c["id"]: c for c in resp.json()}

        result = {}
        for name, cg_id in CRYPTO_CATEGORIES.items():
            cat = cat_lookup.get(cg_id)
            if not cat:
                result[name] = {"error": "not found"}
                continue
            result[name] = {
                "market_cap_usd":  cat.get("market_cap"),
                "volume_24h_usd":  cat.get("volume_24h"),
                "change_1h_%":     cat.get("market_cap_change_1h"),
                "change_24h_%":    cat.get("market_cap_change_24h"),
                "change_7d_%":     cat.get("market_cap_change_7d"),
                "top_3_coins":     cat.get("top_3_coins_id", []),
            }
        return result
    except Exception as e:
        print(f"⚠️ Categories error: {e}")
        return {}


def fetch_crypto_top100() -> list:
    print("📡 Fetching top 100 coins...")
    try:
        params = {
            "vs_currency": "usd",
            "order": "market_cap_desc",
            "per_page": 100,
            "page": 1,
            "price_change_percentage": "24h,7d,30d",
        }
        resp = requests.get("https://api.coingecko.com/api/v3/coins/markets", params=params, timeout=15)
        resp.raise_for_status()

        result = []
        for c in resp.json():
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
        print(f"⚠️ Top 100 error: {e}")
        return []


def identify_movers(top100: list) -> dict:
    if not top100:
        return {}
    gainers_7d = sorted([c for c in top100 if c.get("change_7d_%") is not None],
                        key=lambda x: x["change_7d_%"], reverse=True)[:10]
    losers_7d  = sorted([c for c in top100 if c.get("change_7d_%") is not None],
                        key=lambda x: x["change_7d_%"])[:5]
    near_ath   = [c for c in top100 if (c.get("ath_ratio_%") or 0) >= 85]
    gainers_30d= sorted([c for c in top100 if c.get("change_30d_%") is not None],
                        key=lambda x: x["change_30d_%"], reverse=True)[:10]

    return {
        "top_gainers_7d":  gainers_7d,
        "top_gainers_30d": gainers_30d,
        "top_losers_7d":   losers_7d,
        "near_ath":        near_ath,
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


def extract_signals(categories: dict, movers: dict) -> dict:
    cat_signals = {}
    for cat, data in categories.items():
        if not data or "error" in data:
            continue
        cat_signals[cat] = {
            "change_7d_%":  data.get("change_7d_%"),
            "change_24h_%": data.get("change_24h_%"),
        }

    notable = {}
    for coin in movers.get("top_gainers_7d", [])[:5]:
        notable[coin["symbol"]] = {
            "change_7d_%":  coin.get("change_7d_%"),
            "change_30d_%": coin.get("change_30d_%"),
            "rank":         coin.get("rank"),
            "ath_ratio_%":  coin.get("ath_ratio_%"),
        }

    return {"categories": cat_signals, "notable_coins": notable}


def save_memory(date: str, categories: dict, movers: dict, analysis: str):
    memory = load_memory()
    memory[date] = {
        "signals":       extract_signals(categories, movers),
        "brief_summary": analysis[:600],
    }

    cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).strftime("%Y-%m-%d")
    memory = {k: v for k, v in memory.items() if k >= cutoff}
    memory = dict(sorted(memory.items()))

    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)

    print(f"💾 Crypto memory saved ({len(memory)} entries)")


def build_memory_context(memory: dict) -> str:
    if not memory:
        return "No historical data yet — this is the first run."

    lines = ["HISTORICAL CRYPTO SIGNAL TRACKER:\n"]

    all_cats = set()
    for d in memory.values():
        all_cats.update(d.get("signals", {}).get("categories", {}).keys())

    lines.append("── Category momentum over time (change_7d_%) ──")
    for cat in sorted(all_cats):
        row = f"{cat:25s}"
        for day, day_data in sorted(memory.items())[-10:]:
            val = day_data.get("signals", {}).get("categories", {}).get(cat, {}).get("change_7d_%")
            row += f"  {day[5:]}:{val:+.1f}%" if val is not None else "         —"
        lines.append(row)

    lines.append("\n── Notable coins on radar ──")
    coin_history = {}
    for day, day_data in sorted(memory.items()):
        for symbol, data in day_data.get("signals", {}).get("notable_coins", {}).items():
            if symbol not in coin_history:
                coin_history[symbol] = []
            coin_history[symbol].append({"date": day, **data})

    for symbol, appearances in sorted(coin_history.items()):
        latest = appearances[-1]
        lines.append(
            f"{symbol}: on radar {len(appearances)}x | "
            f"latest 7d={latest.get('change_7d_%', 0):+.1f}% "
            f"rank=#{latest.get('rank')} "
            f"ath={latest.get('ath_ratio_%')}%"
        )

    lines.append("\n── Recent summaries ──")
    for day, day_data in sorted(memory.items())[-5:]:
        summary = day_data.get("brief_summary", "")[:200]
        lines.append(f"\n{day}:\n{summary}...")

    return "\n".join(lines)


# ── AI ANALYSIS ──────────────────────────────────────────────────────────────

def analyze_with_claude(date: str, categories: dict, movers: dict, memory: dict) -> str:
    print("🤖 Running Claude crypto analysis...")

    client         = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    memory_context = build_memory_context(memory)

    prompt = f"""
You are a sharp crypto analyst specializing in identifying EARLY-STAGE sector rotations in the top 100 cryptocurrencies —
the kind of moves that signal where smart money is positioning BEFORE the mainstream narrative forms.
You analyze WEEKLY and MONTHLY timeframes. Ignore 24h noise unless it confirms a multi-week pattern.
A signal is only relevant if it holds across multiple weeks.

Today is {date}.

═══════════════════════════════════════════
CRYPTO SECTOR DATA (by category)
═══════════════════════════════════════════
{json.dumps(categories, indent=2)}

═══════════════════════════════════════════
TOP 100 MOVERS
═══════════════════════════════════════════
{json.dumps(movers, indent=2)}

═══════════════════════════════════════════
HISTORICAL MEMORY
═══════════════════════════════════════════
{memory_context}

═══════════════════════════════════════════
YOUR TASK
═══════════════════════════════════════════

1. 🔥 TOP EMERGING CRYPTO TREND (1-2 categories)
   - Which category is rotating? (L1, meme, AI, RWA, DeFi, etc.)
   - Signal + how long building (reference memory)
   - Macro / narrative catalyst
   - Top coins to watch from that category (rank, symbol, performance)
   - Risk: LOW / MEDIUM / HIGH

2. 📊 CATEGORY SCORECARD
   - All categories: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   - Tag: NEW / CONFIRMED (3+ weeks) / FADING

3. 🔁 SIGNAL UPDATES (memory follow-ups)
   - Follow up on signals from previous runs
   - "X weeks ago flagged Y — confirmed / faded / still building"

4. 💡 EARLY RADAR
   - 1-2 themes too early to call
   - How many weeks on watch, what would confirm the trend?

5. 🏆 TOP 100 STANDOUTS
   - 3-5 coins from top 100 with unusual weekly/monthly momentum
   - Near ATH? Volume breakout? Sector rotation beneficiary?

6. ⚠️ KEY RISKS
   - BTC dominance trend, macro, regulatory, or technical risks

Punchy. Weekly investor timeframe. Use emojis and clear sections. No disclaimers.
"""

    message = client.messages.create(
        model="claude-sonnet-4-6",
        max_tokens=2000,
        messages=[{"role": "user", "content": prompt}]
    )

    return message.content[0].text


# ── EMAIL ────────────────────────────────────────────────────────────────────

def send_email(analysis: str, run_date: str):
    print("📧 Sending email...")

    msg            = MIMEMultipart("alternative")
    msg["Subject"] = f"🪙 Crypto Brief — {run_date}"
    msg["From"]    = SENDER_EMAIL
    msg["To"]      = ", ".join(RECIPIENT_EMAILS)

    text_part = MIMEText(analysis, "plain")
    html_body = analysis.replace("\n", "<br>")
    html = f"""
    <html><body style="font-family: 'Georgia', serif; max-width: 680px; margin: auto;
                        background: #0d0d0d; color: #e8e8e8; padding: 32px;">
      <div style="border-left: 4px solid #f7931a; padding-left: 20px; margin-bottom: 24px;">
        <h1 style="color: #f7931a; font-size: 22px; margin: 0;">🪙 Crypto Trend Brief</h1>
        <p style="color: #888; margin: 4px 0 0;">{run_date}</p>
      </div>
      <div style="line-height: 1.8; font-size: 15px;">{html_body}</div>
      <hr style="border-color: #333; margin-top: 40px;">
      <p style="color: #555; font-size: 12px;">Generated by your Crypto Trend Bot · Not financial advice</p>
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
    print(f"\n🚀 Crypto Trend Bot starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    memory     = load_memory()
    print(f"📚 Loaded {len(memory)} entries of memory")

    today      = datetime.now().strftime("%Y-%m-%d")
    categories = fetch_crypto_categories()
    time.sleep(2)  # CoinGecko rate limit
    top100     = fetch_crypto_top100()
    movers     = identify_movers(top100)

    analysis   = analyze_with_claude(today, categories, movers, memory)

    save_memory(today, categories, movers, analysis)
    send_email(analysis, today)

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()