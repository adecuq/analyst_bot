"""
🪙 Daily Crypto Trend Analyst Bot
- BTC dedicated section
- Top 100 filtered by TVL + protocol fees (via DeFiLlama)
- Categories: AI, Stablecoins, L1, RWA, DeFi, SOL ecosystem, Meme, Privacy + extras
- RSI weekly + MA50/200 weekly + Fibonacci retracement on last major impulse
- Weekly newsletter Monday 7:30 AM Paris
"""

import os
import json
import requests
import time
from datetime import datetime, timedelta
import anthropic
from bot_utils import markdown_to_html, send_email as _send_email

# ── CONFIG ───────────────────────────────────────────────────────────────────
RECIPIENT_EMAILS = [e.strip() for e in os.environ["RECIPIENT_EMAIL"].split(",")]
SENDER_EMAIL     = os.environ["SENDER_EMAIL"]
SENDER_PASSWORD  = os.environ["SENDER_PASSWORD"]
ANTHROPIC_KEY    = os.environ["ANTHROPIC_API_KEY"]

MEMORY_FILE  = "memory_crypto.json"
MEMORY_DAYS  = 180  # 6 months — weekly timeframe
FRED_API_KEY = os.environ.get("FRED_API_KEY", "")

# ── CATEGORIES ───────────────────────────────────────────────────────────────
CRYPTO_CATEGORIES = {
    "Layer 1":          "layer-1",
    "AI Crypto":        "artificial-intelligence",
    "DeFi":             "decentralized-finance-defi",
    "RWA":              "real-world-assets-rwa",
    "SOL Ecosystem":    "solana-ecosystem",
    "Meme Coins":       "meme-token",
    "Privacy":          "privacy-coins",
    "Stablecoins":      "stablecoins",
    "Liquid Staking":   "liquid-staking-tokens",
    "DePin":            "depin",
}

# Meme coins excluded from revenue filter
MEME_CATEGORY = "Meme Coins"

# Minimum thresholds to qualify as "revenue-generating"
MIN_TVL_USD   = 50_000_000   # $50M TVL
MIN_FEES_24H  = 100_000      # $100k fees/day


# ── DEFILLAMA ─────────────────────────────────────────────────────────────────

# Explicit mapping: CoinGecko symbol → DeFiLlama protocol name
# Needed because DeFiLlama indexes by protocol name, not token symbol
SYMBOL_TO_PROTOCOL = {
    "ETH":   "Ethereum",
    "SOL":   "Solana",
    "BNB":   "BSC",
    "AAVE":  "Aave",
    "UNI":   "Uniswap",
    "MKR":   "MakerDAO",
    "COMP":  "Compound",
    "CRV":   "Curve",
    "LDO":   "Lido",
    "LINK":  "Chainlink",
    "ARB":   "Arbitrum",
    "OP":    "Optimism",
    "MATIC": "Polygon",
    "AVAX":  "Avalanche",
    "ADA":   "Cardano",
    "DOT":   "Polkadot",
    "NEAR":  "Near",
    "APT":   "Aptos",
    "SUI":   "Sui",
    "INJ":   "Injective",
    "SEI":   "Sei",
    "TAO":   "Bittensor",
    "PENDLE":"Pendle",
    "HYPE":  "Hyperliquid",
    "JUP":   "Jupiter",
    "ONDO":  "Ondo Finance",
    "ENA":   "Ethena",
    "EIGEN": "EigenLayer",
    "WLD":   "Worldcoin",
    "TRX":   "Tron",
    "TON":   "Ton",
    "XRP":   "Ripple",
    "DYDX":  "dYdX",
    "GMX":   "GMX",
    "SNX":   "Synthetix",
    "FXS":   "Frax",
    "RUNE":  "THORChain",
    "OSMO":  "Osmosis",
    "ATOM":  "Cosmos",
}

def fetch_defillama_fees() -> dict:
    """
    Fetch protocol fees from DeFiLlama.
    Returns dict indexed by BOTH protocol name AND token symbol for easy lookup.
    """
    print("📡 Fetching DeFiLlama protocol fees...")
    result = {}
    try:
        resp = requests.get(
            "https://api.llama.fi/overview/fees"
            "?excludeTotalDataChart=true&excludeTotalDataChartBreakdown=true",
            timeout=30
        )
        resp.raise_for_status()
        protocols = resp.json().get("protocols", [])
        for p in protocols:
            name   = p.get("name", "")
            symbol = p.get("gecko_id", "").upper() or p.get("symbol", "").upper()
            data   = {
                "fees_24h":   p.get("total24h"),
                "fees_7d":    p.get("total7d"),
                "fees_30d":   p.get("total30d"),
                "fees_90d":   p.get("total90d"),
                "fees_1y":    p.get("total1y"),
                "market_cap": p.get("mcap"),
                "protocol":   name,
            }
            # Index by protocol name (uppercase)
            result[name.upper()] = data
            # Also index by token symbol if available
            if symbol:
                result[symbol] = data

        # Add reverse mapping from our explicit table
        for sym, proto_name in SYMBOL_TO_PROTOCOL.items():
            if proto_name.upper() in result and sym not in result:
                result[sym] = result[proto_name.upper()]

        # Note: versioned protocol aggregation (AAVE V2+V3, UNI V3+V4) is handled
        # in fetch_top_revenue_leaders via PROTO_TO_CG_SYMBOL mapping + seen_cg_symbols dedup.
        # No double-merge here.

        print(f"  → {len(result)} fee entries (protocols + symbols)")
    except Exception as e:
        print(f"  ⚠️ DeFiLlama fees error: {e}")
    return result


def fetch_defillama_tvl() -> dict:
    """Fetch TVL per protocol from DeFiLlama."""
    print("📡 Fetching DeFiLlama TVL...")
    result = {}
    try:
        resp = requests.get("https://api.llama.fi/protocols", timeout=30)
        resp.raise_for_status()
        for p in resp.json():
            symbol = p.get("symbol", "").upper()
            tvl    = p.get("tvl") or 0
            if symbol and tvl >= 1_000_000:  # ignore parasitic entries < $1M TVL
                existing = result.get(symbol)
                # Keep the entry with highest TVL if symbol appears multiple times
                if not existing or tvl > (existing.get("tvl") or 0):
                    result[symbol] = {
                        "tvl":      tvl,
                        "tvl_7d_%": p.get("change_7d"),
                        "chain":    p.get("chain"),
                        "category": p.get("category"),
                        "name":     p.get("name"),
                    }
        print(f"  → {len(result)} protocols with TVL data")
    except Exception as e:
        print(f"  ⚠️ DeFiLlama TVL error: {e}")
    return result




def fetch_global_liquidity() -> dict:
    """
    Proxy for Global M2 Liquidity using FRED API (free, no key needed for basic access).
    Uses US M2 + approximation note since true global M2 has no free real-time API.

    Tracks:
    - US M2 (weekly, FRED) — best proxy, highly correlated with BTC
    - Fed Balance Sheet (WALCL) — QE/QT signal
    - Returns 1m, 3m, 12m evolution
    """
    print("📡 Fetching global liquidity data (FRED)...")
    result = {}

    FRED_SERIES = {
        "US_M2":          "M2SL",    # US M2 Money Supply (weekly, billions USD)
        "Fed_Balance":    "WALCL",   # Fed Balance Sheet total assets (weekly, millions USD)
    }

    for name, series_id in FRED_SERIES.items():
        try:
            # FRED official API — free, no key required for public series
            fred_key = FRED_API_KEY
            if not fred_key:
                print(f"  ⚠️ FRED_API_KEY not set — skipping {series_id}")
                result[name] = {"error": "FRED_API_KEY not configured"}
                continue
            url  = (
                f"https://api.stlouisfed.org/fred/series/observations"
                f"?series_id={series_id}&file_type=json"
                f"&sort_order=asc&limit=500"
                f"&api_key={fred_key}"
            )
            hdrs = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
            resp = requests.get(url, headers=hdrs, timeout=15)
            resp.raise_for_status()
            raw  = resp.json()

            observations = [
                (d["date"], float(d["value"]))
                for d in raw.get("observations", [])
                if d.get("value") not in (".", "", None)
            ]

            if not observations:
                continue

            observations.sort(key=lambda x: x[0])
            current_val  = observations[-1][1]
            current_date = observations[-1][0]

            def find_ago(obs, days):
                from datetime import datetime, timedelta
                target = (datetime.strptime(obs[-1][0], "%Y-%m-%d") - timedelta(days=days)).strftime("%Y-%m-%d")
                # Find closest observation
                candidates = [(d, v) for d, v in obs if d <= target]
                return candidates[-1][1] if candidates else None

            val_1m  = find_ago(observations, 30)
            val_3m  = find_ago(observations, 90)
            val_12m = find_ago(observations, 365)

            def pct_change(current, past):
                if past and past != 0:
                    return round((current - past) / past * 100, 2)
                return None

            result[name] = {
                "current":      round(current_val, 1),
                "date":         current_date,
                "change_1m_%":  pct_change(current_val, val_1m),
                "change_3m_%":  pct_change(current_val, val_3m),
                "change_12m_%": pct_change(current_val, val_12m),
                "unit":         "billions USD" if series_id == "M2SL" else "millions USD",
                "trend":        "EXPANDING" if (pct_change(current_val, val_3m) or 0) > 0
                                else "CONTRACTING",
            }
            time.sleep(0.5)

        except Exception as e:
            print(f"  ⚠️ FRED {name} error: {e}")
            result[name] = {"error": str(e)}

    # Summary signal for Claude
    m2 = result.get("US_M2", {})
    fed = result.get("Fed_Balance", {})
    if m2 and "trend" in m2:
        liquidity_signal = "EXPANDING" if m2.get("change_3m_%", 0) > 0 else "CONTRACTING"
        result["signal"] = liquidity_signal
        result["note"] = (
            "US M2 + Fed Balance Sheet — best free proxy for global liquidity. "
            "True global M2 (Fed+ECB+BoJ+PBoC) has no free real-time API. "
            "Historical correlation with BTC: expanding M2 = bullish tailwind."
        )

    print(f"  → Liquidity data: M2={m2.get('current','?')}B, trend={m2.get('trend','?')}")
    return result


def fetch_fear_greed() -> dict:
    """Fetch Fear & Greed Index from alternative.me (free, no key)."""
    print("📡 Fetching Fear & Greed Index...")
    try:
        resp = requests.get("https://api.alternative.me/fng/?limit=30", timeout=10)
        resp.raise_for_status()
        data = resp.json().get("data", [])
        if not data:
            return {}
        current   = data[0]
        week_ago  = data[7]  if len(data) > 7  else None
        month_ago = data[29] if len(data) > 29 else None

        def classify(val):
            v = int(val)
            if v >= 75: return "Extreme Greed 🤑"
            if v >= 55: return "Greed 😀"
            if v >= 45: return "Neutral 😐"
            if v >= 25: return "Fear 😨"
            return "Extreme Fear 😱"

        return {
            "value":          int(current["value"]),
            "label":          classify(current["value"]),
            "value_1w_ago":   int(week_ago["value"]) if week_ago else None,
            "label_1w_ago":   classify(week_ago["value"]) if week_ago else None,
            "value_1m_ago":   int(month_ago["value"]) if month_ago else None,
            "label_1m_ago":   classify(month_ago["value"]) if month_ago else None,
            "change_1w":      int(current["value"]) - int(week_ago["value"]) if week_ago else None,
            "change_1m":      int(current["value"]) - int(month_ago["value"]) if month_ago else None,
        }
    except Exception as e:
        print(f"  ⚠️ Fear & Greed error: {e}")
        return {}


def fetch_funding_rates() -> dict:
    """Fetch BTC & ETH funding rates from Coinglass (free tier, no key needed)."""
    print("📡 Fetching funding rates & open interest...")
    result = {}
    try:
        # Coinglass public endpoint
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/funding",
            headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for item in data:
            symbol = item.get("symbol", "").upper()
            if symbol in ("BTC", "ETH", "SOL"):
                rates = item.get("uMarginList", [])
                # Average funding rate across exchanges
                avg_rate = None
                if rates:
                    vals = [r.get("rate") for r in rates if r.get("rate") is not None]
                    avg_rate = round(sum(vals) / len(vals) * 100, 4) if vals else None
                result[symbol] = {
                    "avg_funding_rate_%": avg_rate,
                    "signal": (
                        "OVERLEVERAGED_LONG 🚨" if (avg_rate or 0) > 0.05 else
                        "OVERLEVERAGED_SHORT 🚨" if (avg_rate or 0) < -0.02 else
                        "NEUTRAL ✅"
                    ),
                }
        print(f"  → Funding rates: {list(result.keys())}")
    except Exception as e:
        print(f"  ⚠️ Funding rate error: {e}")

    # Open Interest from Coinglass
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/open_interest",
            headers=headers, timeout=10
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
        for item in data:
            symbol = item.get("symbol", "").upper()
            if symbol in ("BTC", "ETH", "SOL"):
                if symbol not in result:
                    result[symbol] = {}
                result[symbol]["open_interest_usd"] = item.get("openInterest")
                result[symbol]["oi_change_24h_%"]   = item.get("openInterestChangePercent24h")
    except Exception as e:
        print(f"  ⚠️ Open Interest error: {e}")

    return result


def fetch_stablecoin_supply() -> dict:
    """Track USDT + USDC market cap trend — rising = dry powder incoming."""
    print("📡 Fetching stablecoin supply...")
    try:
        params = {
            "vs_currency": "usd",
            "ids": "tether,usd-coin,dai,ethena-usde,first-digital-usd",
            "order": "market_cap_desc",
            "per_page": 10,
            "page": 1,
            "price_change_percentage": "7d,30d",
        }
        resp = requests.get("https://api.coingecko.com/api/v3/coins/markets",
                           params=params, timeout=10)
        resp.raise_for_status()
        coins = resp.json()
        total = sum(c.get("market_cap") or 0 for c in coins)
        breakdown = {
            c["symbol"].upper(): {
                "market_cap":   c.get("market_cap"),
                "change_7d_%":  round(c.get("price_change_percentage_7d_in_currency") or 0, 2),
                "change_30d_%": round(c.get("price_change_percentage_30d_in_currency") or 0, 2),
            }
            for c in coins
        }
        # Trend signal
        avg_30d = sum(v.get("change_30d_%") or 0 for v in breakdown.values()) / len(breakdown)
        return {
            "total_supply_usd": total,
            "breakdown":        breakdown,
            "trend_30d":        "GROWING" if avg_30d > 0.5 else "SHRINKING" if avg_30d < -0.5 else "STABLE",
            "signal":           (
                "🟢 DRY POWDER GROWING — cash on sidelines, potential inflow" if avg_30d > 0.5 else
                "🔴 STABLECOIN OUTFLOW — capital deploying or exiting crypto" if avg_30d < -0.5 else
                "⚪ STABLE — no strong signal"
            ),
        }
    except Exception as e:
        print(f"  ⚠️ Stablecoin error: {e}")
        return {}


def fetch_upcoming_catalysts() -> dict:
    """
    Ask Claude to provide upcoming macro catalysts based on its knowledge.
    Returns structured prompt context — not fetched from API.
    """
    from datetime import datetime
    return {
        "note": "Catalysts below are from Claude knowledge — verify dates independently.",
        "week": datetime.now().strftime("%Y-%W"),
    }


def enrich_category_7d(categories: dict, top300: list) -> dict:
    """
    Compute average 7d price change per category using top300 coin data.
    Fills in missing change_7d_% from CoinGecko categories endpoint.
    """
    # Map category names to coin symbols using top_3_coins as anchor
    cat_coins = {
        "Layer 1":       ["ETH", "SOL", "BNB", "ADA", "AVAX", "TRX", "DOT", "NEAR", "APT", "SUI"],
        "AI Crypto":     ["TAO", "RENDER", "FET", "AGIX", "OCEAN", "WLD", "GRT", "NMR", "PAAL"],
        "DeFi":          ["UNI", "AAVE", "MKR", "CRV", "LDO", "PENDLE", "GMX", "DYDX", "SNX", "RUNE"],
        "RWA":           ["ONDO", "MKR", "POLYX", "RIO", "CFG", "CPOOL"],
        "SOL Ecosystem": ["SOL", "JUP", "BONK", "RAY", "PYTH", "WIF", "DRIFT", "JTO"],
        "Meme Coins":    ["DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "BRETT", "POPCAT"],
        "Privacy":       ["XMR", "ZEC", "SCRT", "ROSE", "KEEP", "NYM"],
        "Stablecoins":   ["USDT", "USDC", "DAI", "USDE", "FDUSD"],
        "Liquid Staking":["LDO", "RPL", "SFRXETH", "ANKR", "SWISE"],
        "DePin":         ["FIL", "HNT", "RNDR", "MOBILE", "AR", "GEODNET"],
    }

    coin_map = {c["symbol"]: c for c in top300}

    for cat_name, symbols in cat_coins.items():
        if cat_name not in categories:
            continue
        # If 7d already available and non-null, skip
        if categories[cat_name].get("change_7d_%") is not None:
            continue
        # Compute average 7d from matching coins
        vals = [coin_map[s]["change_7d_%"] for s in symbols
                if s in coin_map and coin_map[s].get("change_7d_%") is not None]
        if vals:
            categories[cat_name]["change_7d_%"] = round(sum(vals) / len(vals), 2)

    return categories

# ── NEW FETCH FUNCTIONS ───────────────────────────────────────────────────────

def fetch_etf_flows() -> dict:
    """
    Fetch BTC ETF flows from Coinglass public API (primary)
    + Farside Investors HTML scraping (fallback).
    """
    print("📡 Fetching ETF flows...")
    result = {}

    # Primary: Coinglass ETF flow endpoint
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/etf/bitcoin/flow",
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                latest = data[-1] if isinstance(data, list) else data
                result["BTC"] = {
                    "flow_today_usd":  latest.get("netFlow") or latest.get("net_flow"),
                    "cumulative_7d":   sum(d.get("netFlow") or 0 for d in (data[-7:] if isinstance(data, list) else [])),
                    "source":          "Coinglass",
                }
                print(f"  → ETF BTC flow: ${result['BTC'].get('flow_today_usd', 'N/A')}")
    except Exception as e:
        print(f"  ⚠️ Coinglass ETF error: {e}")

    # Fallback: Farside Investors
    if not result.get("BTC", {}).get("flow_today_usd"):
        try:
            from html.parser import HTMLParser
            headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
            resp = requests.get("https://farside.co.uk/bitcoin-etf-flow-all-data/",
                               headers=headers, timeout=15)
            if resp.status_code == 200:
                class TP(HTMLParser):
                    def __init__(self):
                        super().__init__(); self.rows=[]; self.row=[]; self.cell=""; self.in_cell=False
                    def handle_starttag(self, t, a):
                        if t in ("td","th"): self.in_cell=True; self.cell=""
                        elif t=="tr": self.row=[]
                    def handle_endtag(self, t):
                        if t in ("td","th"): self.in_cell=False; self.row.append(self.cell.strip())
                        elif t=="tr" and self.row: self.rows.append(self.row)
                    def handle_data(self, d):
                        if self.in_cell: self.cell+=d

                p = TP(); p.feed(resp.text)
                data_rows = [r for r in p.rows if len(r)>5 and r[0] and r[0][:2].isdigit()]
                if data_rows:
                    last = data_rows[-1]
                    try:
                        raw = last[-1].replace(",","").replace("$","").strip()
                        total_m = float(raw) if raw not in ("-","","—") else 0
                        result["BTC"] = {
                            "flow_today_usd":  total_m * 1_000_000,
                            "flow_today_m":    total_m,
                            "cumulative_7d":   None,
                            "source":          "Farside",
                            "date":            last[0],
                        }
                        print(f"  → ETF BTC (Farside): ${total_m}M")
                    except Exception:
                        pass
        except Exception as e:
            print(f"  ⚠️ Farside ETF error: {e}")

    if not result:
        print("  → ETF flows: unavailable")
    return result


def fetch_exchange_netflow() -> dict:
    """
    Fetch BTC exchange netflow (inflow - outflow) from CryptoQuant public API.
    Positive = net inflow to exchanges (selling pressure)
    Negative = net outflow from exchanges (accumulation / hodl)
    """
    print("📡 Fetching BTC exchange netflow...")
    try:
        # CryptoQuant free public endpoint
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(
            "https://api.cryptoquant.com/v1/btc/exchange-flows/netflow",
            params={"window": "day", "limit": 30},
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json().get("result", {}).get("data", [])
            if data:
                latest   = data[-1]
                prev_7   = data[-8] if len(data) >= 8  else data[0]
                prev_30  = data[0]

                def classify_netflow(val):
                    if val is None: return "N/A"
                    if val > 5000:  return "STRONG INFLOW 🔴 (sell pressure)"
                    if val > 1000:  return "INFLOW ⚠️ (moderate sell)"
                    if val < -5000: return "STRONG OUTFLOW 🟢 (accumulation)"
                    if val < -1000: return "OUTFLOW 🟢 (mild accumulation)"
                    return "NEUTRAL ➡️"

                netflow = latest.get("netflow_total")
                return {
                    "netflow_24h_btc":  netflow,
                    "netflow_7d_btc":   sum(d.get("netflow_total", 0) or 0 for d in data[-7:]),
                    "signal":           classify_netflow(netflow),
                    "source":           "CryptoQuant",
                }
    except Exception as e:
        print(f"  ⚠️ CryptoQuant netflow error: {e}")

    # Fallback: use Coinglass exchange reserve data
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/indicator/exchange_balance",
            params={"symbol": "BTC"},
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                latest = data[-1]
                change = latest.get("changePercent24h")
                return {
                    "exchange_reserve_btc": latest.get("balance"),
                    "reserve_change_24h_%": change,
                    "signal": (
                        "INFLOW 🔴 (sell pressure)" if (change or 0) > 0.5 else
                        "OUTFLOW 🟢 (accumulation)" if (change or 0) < -0.5 else
                        "NEUTRAL ➡️"
                    ),
                    "source": "Coinglass",
                }
    except Exception as e:
        print(f"  ⚠️ Exchange netflow fallback error: {e}")

    return {}


def fetch_liquidations() -> dict:
    """Fetch 24h liquidations + long/short ratio from Coinglass."""
    print("📡 Fetching liquidations & long/short ratio...")
    result = {}
    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        # Liquidations
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/liquidation_history",
            params={"symbol": "BTC", "time_type": "h24"},
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json().get("data", {})
            result["liquidations"] = {
                "long_liq_usd":  data.get("buyVolUsd"),   # longs liquidated
                "short_liq_usd": data.get("sellVolUsd"),  # shorts liquidated
                "total_liq_usd": data.get("volUsd"),
            }
    except Exception as e:
        print(f"  ⚠️ Liquidations error: {e}")

    try:
        headers = {"User-Agent": "Mozilla/5.0 (compatible; research-bot/1.0)"}
        # Long/Short ratio
        resp = requests.get(
            "https://open-api.coinglass.com/public/v2/indicator/long_short",
            params={"symbol": "BTC", "time_type": "h24"},
            headers=headers, timeout=10
        )
        if resp.status_code == 200:
            data = resp.json().get("data", [])
            if data:
                latest = data[-1]
                long_pct  = latest.get("longRatio", 0) * 100
                short_pct = latest.get("shortRatio", 0) * 100
                result["long_short"] = {
                    "long_%":   round(long_pct, 1),
                    "short_%":  round(short_pct, 1),
                    "signal":   (
                        "MAJORITY LONG 🐂" if long_pct > 55 else
                        "MAJORITY SHORT 🐻" if short_pct > 55 else
                        "BALANCED ➡️"
                    ),
                }
    except Exception as e:
        print(f"  ⚠️ Long/short error: {e}")

    print(f"  → Liquidations: {result.get('liquidations', {}).get('total_liq_usd', 'N/A')}")
    return result


def fetch_correlations(top100: list) -> dict:
    """
    Compute BTC correlation with SPX and Gold over 30d and 90d.
    Uses Yahoo Finance via yfinance-style CoinGecko + SPX/Gold price data.
    """
    print("📡 Fetching correlations (BTC/SPX, BTC/Gold)...")
    try:
        import math

        def get_prices(cg_id: str, days: int) -> list:
            resp = requests.get(
                f"https://api.coingecko.com/api/v3/coins/{cg_id}/market_chart",
                params={"vs_currency": "usd", "days": days, "interval": "daily"},
                timeout=15
            )
            resp.raise_for_status()
            return [p[1] for p in resp.json().get("prices", [])]

        def get_tradfi_prices(ticker: str, days: int) -> list:
            """Fetch SPX or Gold prices from Yahoo Finance v8 API."""
            import urllib.request, urllib.error
            end   = int(datetime.now().timestamp())
            start = int((datetime.now() - timedelta(days=days + 10)).timestamp())
            # Try v8 first, then v7
            for base in [
                f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker}",
                f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}",
            ]:
                try:
                    url = f"{base}?period1={start}&period2={end}&interval=1d&includePrePost=false"
                    req = urllib.request.Request(url, headers={
                        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7)",
                        "Accept": "application/json",
                    })
                    with urllib.request.urlopen(req, timeout=12) as r:
                        data   = json.loads(r.read())
                    closes = data["chart"]["result"][0]["indicators"]["quote"][0]["close"]
                    return [c for c in closes if c is not None]
                except Exception:
                    continue
            return []

        def pearson_corr(x: list, y: list) -> float:
            n = min(len(x), len(y))
            x, y = x[-n:], y[-n:]
            mx, my = sum(x)/n, sum(y)/n
            num = sum((xi-mx)*(yi-my) for xi, yi in zip(x, y))
            den = math.sqrt(sum((xi-mx)**2 for xi in x) * sum((yi-my)**2 for yi in y))
            return round(num/den, 3) if den else 0

        btc_30  = get_prices("bitcoin", 30)
        time.sleep(6)
        btc_90  = get_prices("bitcoin", 90)
        time.sleep(6)
        spx_30  = get_tradfi_prices("^GSPC", 30)
        time.sleep(2)
        spx_90  = get_tradfi_prices("^GSPC", 90)
        time.sleep(2)
        gold_30 = get_tradfi_prices("GC=F", 30)
        time.sleep(2)
        gold_90 = get_tradfi_prices("GC=F", 90)

        corr_btc_spx_30  = pearson_corr(btc_30,  spx_30)
        corr_btc_spx_90  = pearson_corr(btc_90,  spx_90)
        corr_btc_gold_30 = pearson_corr(btc_30,  gold_30)
        corr_btc_gold_90 = pearson_corr(btc_90,  gold_90)

        def interp_corr(r):
            if r > 0.7:  return "STRONG POSITIVE — moves with"
            if r > 0.4:  return "MODERATE POSITIVE — tends to follow"
            if r > 0.1:  return "WEAK POSITIVE"
            if r > -0.1: return "UNCORRELATED"
            if r > -0.4: return "WEAK NEGATIVE"
            if r > -0.7: return "MODERATE NEGATIVE — tends to diverge"
            return "STRONG NEGATIVE — inverse"

        result = {
            "BTC_SPX":  {
                "corr_30d": corr_btc_spx_30,
                "corr_90d": corr_btc_spx_90,
                "signal_30d": interp_corr(corr_btc_spx_30),
                "note": "High corr = BTC follows risk-off macro; low = independent move",
            },
            "BTC_GOLD": {
                "corr_30d": corr_btc_gold_30,
                "corr_90d": corr_btc_gold_90,
                "signal_30d": interp_corr(corr_btc_gold_30),
                "note": "Rising corr = BTC acting as digital gold / safe haven",
            },
        }
        print(f"  → BTC/SPX 30d: {corr_btc_spx_30} | BTC/Gold 30d: {corr_btc_gold_30}")
        return result

    except Exception as e:
        print(f"  ⚠️ Correlation error: {e}")
        return {}


def fetch_global_market() -> dict:
    """
    Fetch BTC dominance + 1w/1m evolution.
    Uses CoinGecko /global for current dominance + bitcoin market_chart for history.
    """
    print("📡 Fetching global market data...")
    try:
        # Current snapshot
        resp = requests.get("https://api.coingecko.com/api/v3/global", timeout=10)
        resp.raise_for_status()
        data       = resp.json().get("data", {})
        mcap_pct   = data.get("market_cap_percentage", {})
        total_mcap = data.get("total_market_cap", {}).get("usd") or 0
        btc_dom    = round(mcap_pct.get("btc", 0), 1)

        print(f"  → BTC dominance (current): {btc_dom}%")

        # Historical BTC + total market cap via bitcoin market_chart
        time.sleep(1)
        hist_resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/market_chart",
            params={"vs_currency": "usd", "days": "31", "interval": "daily"},
            timeout=15
        )
        hist_resp.raise_for_status()
        hist      = hist_resp.json()
        btc_mcaps = hist.get("market_caps", [])   # [[ts, mcap], ...]
        # total_market_caps is also returned by this endpoint
        tot_mcaps = hist.get("total_volumes", []) # fallback — we use ratio instead

        # Compute dominance at a past date using BTC mcap + current total as proxy
        # More accurate: use btc_mcap_past / (btc_mcap_past / btc_dom_current * 100)
        # Simplest reliable approach: dom_past = btc_mcap_past / btc_mcap_now * btc_dom_now
        btc_now = btc_mcaps[-1][1] if btc_mcaps else None

        def dom_at_days(days_ago: int) -> object:
            try:
                idx      = -(days_ago + 1)
                btc_past = btc_mcaps[idx][1]
                if btc_now and btc_past:
                    return round(btc_dom * (btc_past / btc_now), 1)
                return None
            except Exception:
                return None

        dom_1w = dom_at_days(7)
        dom_1m = dom_at_days(30)

        print(f"  → BTC dom 1w ago: {dom_1w}% | 1m ago: {dom_1m}%")

        return {
            "btc_dominance_%":         btc_dom,
            "btc_dominance_1w_ago_%":  dom_1w,
            "btc_dominance_1m_ago_%":  dom_1m,
            "btc_dom_change_1w":       round(btc_dom - dom_1w, 1) if dom_1w else None,
            "btc_dom_change_1m":       round(btc_dom - dom_1m, 1) if dom_1m else None,
            "eth_dominance_%":         round(mcap_pct.get("eth", 0), 1),
            "total_market_cap":        total_mcap,
            "total_volume_24h":        data.get("total_volume", {}).get("usd"),
            "market_cap_change_24h_%": round(data.get("market_cap_change_percentage_24h_usd", 0), 2),
            "altcoin_market_cap":      None,
        }
    except Exception as e:
        print(f"  ⚠️ Global market error: {e}")
        return {}

# ── COINGECKO ─────────────────────────────────────────────────────────────────

def fetch_crypto_categories() -> dict:
    print("📡 Fetching CoinGecko categories...")
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
            # market_cap_change_7d is often None from CoinGecko categories endpoint
            # Use market_cap_change_24h as fallback, 7d computed later from top300
            result[name] = {
                "market_cap_usd": cat.get("market_cap"),
                "volume_24h_usd": cat.get("volume_24h"),
                "change_24h_%":   cat.get("market_cap_change_24h"),
                "change_7d_%":    cat.get("market_cap_change_7d"),  # enriched later
                "top_3_coins":    cat.get("top_3_coins_id", []),
            }
        return result
    except Exception as e:
        print(f"  ⚠️ CoinGecko categories error: {e}")
        return {}


def fetch_crypto_top300() -> list:
    """Fetch top 300 coins across 3 pages — covers meme leaders in rank 100-300."""
    print("📡 Fetching CoinGecko top 300 (3 pages)...")
    result = []
    for page in range(1, 2):  # 1 page = top 100, avoids 429 on free tier
        try:
            params = {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 100,
                "page": page,
                "price_change_percentage": "24h,7d,30d",
            }
            resp = requests.get("https://api.coingecko.com/api/v3/coins/markets", params=params, timeout=15)
            resp.raise_for_status()
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
                    "ath":          c.get("ath"),
                    "ath_ratio_%":  round((c.get("current_price", 0) / c.get("ath", 1)) * 100, 1) if c.get("ath") else None,
                })
            time.sleep(5)    # CoinGecko rate limit — free tier is strict
        except Exception as e:
            print(f"  ⚠️ CoinGecko page {page} error: {e}")
    print(f"  → {len(result)} coins fetched")
    return result

# Keep alias for backward compat
def fetch_crypto_top100() -> list:
    """Top 100 coins — HYPE and other high-fee protocols handled via fetch_top_revenue_leaders."""
    return fetch_crypto_top300()


def fetch_btc_ohlc() -> list:
    """Fetch BTC daily OHLC for 90 days (for Fibonacci + RSI weekly)."""
    print("📡 Fetching BTC OHLC data...")
    try:
        resp = requests.get(
            "https://api.coingecko.com/api/v3/coins/bitcoin/ohlc",
            params={"vs_currency": "usd", "days": "90"},
            timeout=15
        )
        resp.raise_for_status()
        # Returns [timestamp, open, high, low, close]
        return resp.json()
    except Exception as e:
        print(f"  ⚠️ BTC OHLC error: {e}")
        return []


# ── TECHNICAL ANALYSIS ────────────────────────────────────────────────────────

def compute_rsi(closes: list, period: int = 14) -> object:
    """Compute RSI from a list of close prices."""
    if len(closes) < period + 1:
        return None
    try:
        deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
        gains  = [max(d, 0) for d in deltas]
        losses = [abs(min(d, 0)) for d in deltas]
        avg_gain = sum(gains[-period:]) / period
        avg_loss = sum(losses[-period:]) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return round(100 - (100 / (1 + rs)), 1)
    except Exception:
        return None


def compute_ma(closes: list, period: int):
    if len(closes) < period:
        return None
    return round(sum(closes[-period:]) / period, 2)


def compute_fibonacci(ohlc: list) -> dict:
    """
    Detect last major impulse (swing high → swing low or vice versa)
    on 90-day daily data and compute Fibonacci retracement levels.
    Uses significant swing = price move > 15% from local extreme.
    """
    if len(ohlc) < 20:
        return {}
    try:
        closes = [c[4] for c in ohlc]  # close prices
        highs  = [c[2] for c in ohlc]
        lows   = [c[3] for c in ohlc]

        # Find last significant swing high and low
        recent_high = max(highs[-60:])
        recent_low  = min(lows[-60:])

        high_idx = highs[-60:].index(recent_high)
        low_idx  = lows[-60:].index(recent_low)

        # Determine direction of last impulse
        if high_idx > low_idx:
            # Upward impulse: low → high
            swing_low  = recent_low
            swing_high = recent_high
            direction  = "UP"
        else:
            # Downward impulse: high → low
            swing_low  = recent_low
            swing_high = recent_high
            direction  = "DOWN"

        diff = swing_high - swing_low
        current = closes[-1]

        # Fibonacci levels
        levels = {
            "0%":    round(swing_low, 2),
            "23.6%": round(swing_high - 0.236 * diff, 2),
            "38.2%": round(swing_high - 0.382 * diff, 2),
            "50%":   round(swing_high - 0.500 * diff, 2),
            "61.8%": round(swing_high - 0.618 * diff, 2),
            "78.6%": round(swing_high - 0.786 * diff, 2),
            "100%":  round(swing_high, 2),
        }

        # Find nearest support/resistance
        level_values = sorted(levels.values())
        nearest_support    = max((l for l in level_values if l <= current), default=None)
        nearest_resistance = min((l for l in level_values if l > current), default=None)

        return {
            "direction":          direction,
            "swing_low":          swing_low,
            "swing_high":         swing_high,
            "current_price":      round(current, 2),
            "levels":             levels,
            "nearest_support":    nearest_support,
            "nearest_resistance": nearest_resistance,
        }
    except Exception as e:
        return {"error": str(e)}


def analyze_btc(top100: list, ohlc: list) -> dict:
    """Full BTC technical analysis: RSI, MA, Fibonacci."""
    btc = next((c for c in top100 if c["symbol"] == "BTC"), {})
    if not btc or not ohlc:
        return btc

    closes_daily = [c[4] for c in ohlc]

    # Weekly closes (sample every 7 days)
    closes_weekly = closes_daily[::7]

    rsi_daily  = compute_rsi(closes_daily, 14)
    rsi_weekly = compute_rsi(closes_weekly, 14)
    ma50_w     = compute_ma(closes_weekly, 50)
    ma200_w    = compute_ma(closes_weekly, 200)
    fibo       = compute_fibonacci(ohlc)

    rsi_zone = None
    if rsi_weekly:
        if rsi_weekly >= 75:    rsi_zone = "OVERBOUGHT"
        elif rsi_weekly >= 60:  rsi_zone = "EXTENDED"
        elif rsi_weekly <= 30:  rsi_zone = "OVERSOLD"
        elif rsi_weekly <= 45:  rsi_zone = "NEUTRAL_LOW"
        else:                   rsi_zone = "NEUTRAL"

    ma_signal = None
    if ma50_w and ma200_w:
        ma_signal = "ABOVE_200" if ma50_w > ma200_w else "BELOW_200"

    # ETH/BTC ratio from top100
    eth = next((c for c in top100 if c["symbol"] == "ETH"), {})
    eth_btc_ratio = None
    if eth.get("price") and btc.get("price"):
        eth_btc_ratio = round(eth["price"] / btc["price"], 5)

    return {
        **btc,
        "rsi_daily":    rsi_daily,
        "rsi_weekly":   rsi_weekly,
        "rsi_zone":     rsi_zone,
        "ma50_weekly":  ma50_w,
        "ma200_weekly": ma200_w,
        "ma_signal":    ma_signal,
        "fibonacci":    fibo,
        "eth_btc_ratio": eth_btc_ratio,
        "eth_7d_%":     eth.get("change_7d_%"),
        "eth_30d_%":    eth.get("change_30d_%"),
    }


# ── FILTER & ENRICH TOP 100 ───────────────────────────────────────────────────

# Known meme coins
KNOWN_MEMES = {
    "DOGE", "SHIB", "PEPE", "FLOKI", "BONK", "WIF", "MEME",
    "BRETT", "POPCAT", "MOG", "TURBO", "NEIRO", "GOAT", "TRUMP", "MELANIA",
    "BABYDOGE", "LADYS", "ELON", "BITCOIN", "MONKY", "SLERF",
    "BOME", "MYRO", "PONKE", "GIGA", "PNUT", "ACT", "CHILLGUY",
}

# Coins that generate real revenue — always include regardless of DeFiLlama match
# These are L1s and major DeFi protocols with known on-chain revenue
KNOWN_REVENUE = {
    "ETH", "SOL", "BNB", "TRX", "TON", "ADA", "AVAX", "DOT", "NEAR",
    "MATIC", "ARB", "OP", "APT", "SUI", "INJ", "SEI", "ATOM",
    "UNI", "AAVE", "MKR", "COMP", "CRV", "SNX", "LDO", "PENDLE",
    "JUP", "ONDO", "ENA", "DYDX", "GMX", "RUNE", "OSMO", "HYPE",
    "LINK", "GRT", "FIL", "AR", "RENDER", "TAO", "WLD",
    "XRP", "ADA", "ALGO",
}

def lookup_fees(symbol: str, name: str, fees_data: dict) -> dict:
    """Try multiple keys to find fees data for a coin."""
    # Try direct symbol
    if fees_data.get(symbol):
        return fees_data[symbol]
    # Try mapped protocol name
    proto = SYMBOL_TO_PROTOCOL.get(symbol, "")
    if proto and fees_data.get(proto.upper()):
        return fees_data[proto.upper()]
    # Try coin name variations
    for key in [name.upper(), name.replace(" ", "").upper()]:
        if fees_data.get(key):
            return fees_data[key]
    return {}

def lookup_tvl(symbol: str, name: str, tvl_data: dict) -> dict:
    """Try multiple keys to find TVL data for a coin."""
    if tvl_data.get(symbol):
        return tvl_data[symbol]
    proto = SYMBOL_TO_PROTOCOL.get(symbol, "")
    if proto and tvl_data.get(proto.upper()):
        return tvl_data[proto.upper()]
    for key in [name.upper(), name.replace(" ", "").upper()]:
        if tvl_data.get(key):
            return tvl_data[key]
    return {}


def enrich_and_filter(top100: list, tvl_data: dict, fees_data: dict, categories: dict) -> dict:
    """
    Filter top 100 to revenue-generating projects.
    Three buckets:
    1. Known revenue projects (L1s, major DeFi) — always included
    2. Others with TVL > $50M or fees > $100k/day from DeFiLlama
    3. Meme coins — separate bucket, no revenue filter
    """
    revenue_coins = []
    meme_coins    = []

    for coin in top100:
        symbol = coin["symbol"]
        name   = coin.get("name", "")

        if symbol == "BTC":
            continue  # BTC handled separately

        fees_info = lookup_fees(symbol, name, fees_data)
        tvl_info  = lookup_tvl(symbol, name, tvl_data)

        tvl      = tvl_info.get("tvl") or 0
        fees_24h = fees_info.get("fees_24h") or 0

        enriched = {
            **coin,
            "tvl":        tvl_info.get("tvl"),
            "tvl_7d_%":   tvl_info.get("tvl_7d_%"),
            "fees_24h":   fees_info.get("fees_24h"),
            "fees_7d":    fees_info.get("fees_7d"),
            "fees_30d":   fees_info.get("fees_30d"),
            "fees_90d":   fees_info.get("fees_90d"),
            "fees_1y":    fees_info.get("fees_1y"),
            "fees_mcap":  fees_info.get("market_cap"),
        }

        rank = coin.get("rank") or 999
        # Explicit exclusions — L1s/legit projects that match meme keywords by accident
        meme_exclusions = {"TON", "ATOM", "NEAR", "BONE", "CAT", "MOON", "ELON", "DOGS", "M", "CC", "SUI", "SEI", "APT"}
        if symbol in KNOWN_MEMES or (rank <= 300 and symbol not in meme_exclusions and any(
            kw in name.lower() for kw in ["inu", "doge", "pepe", "cat", "frog", "meme",
                                            "moon", "elon", "baby", "chad", "wojak", "bonk"]
        )):
            meme_coins.append(enriched)
        elif symbol in KNOWN_REVENUE or tvl >= MIN_TVL_USD or fees_24h >= MIN_FEES_24H:
            revenue_coins.append(enriched)
        # else: filtered out

    revenue_coins.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
    meme_coins.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)

    return {
        "revenue_generating": revenue_coins,
        "meme_coins":         meme_coins,
        "filtered_out_count": len(top100) - 1 - len(revenue_coins) - len(meme_coins),
    }



def fetch_top_revenue_leaders(fees_data: dict, tvl_data: dict, top100: list = None) -> list:
    """
    Option 2: Build Revenue Leaders directly from DeFiLlama top fees,
    enriched with CoinGecko market data from top100 already fetched.
    """
    print("📡 Building Revenue Leaders from DeFiLlama top fees...")

    # 1. Get top 25 unique protocols by fees_1y from fees_data
    # fees_data is indexed by both name and symbol — deduplicate by protocol name
    seen_protocols = set()
    top_by_fees = []
    for key, data in fees_data.items():
        proto = data.get("protocol", key)
        if proto in seen_protocols:
            continue
        fees_1y = data.get("fees_1y") or 0
        if fees_1y > 0:
            seen_protocols.add(proto)
            top_by_fees.append({**data, "_key": key})

    top_by_fees.sort(key=lambda x: x.get("fees_1y") or 0, reverse=True)
    top_by_fees = top_by_fees[:25]

    # Protocol name → CoinGecko symbol mapping for protocols with unusual names
    PROTO_TO_CG_SYMBOL = {
        "HYPERLIQUID": "HYPE",
        "UNISWAP V2":  "UNI",
        "UNISWAP V3":  "UNI",
        "UNISWAP V4":  "UNI",
        "UNISWAP":     "UNI",
        "AAVE V2":     "AAVE",
        "AAVE V3":     "AAVE",
        "LIDO":        "LDO",
        "MAKERDAO":    "MKR",
        "PUMPSWAP":    "SOL",   # Solana DEX — proxy
        "PUMP.FUN":    "SOL",   # Solana DEX — proxy
        "FRAGMENT":    "TON",   # TON ecosystem
        "TETHER":      "USDT",
        "BSC":         "BNB",
        "SOLANA":      "SOL",
        "ETHEREUM":    "ETH",
        "TRON":        "TRX",
        "BITCOIN":     "BTC",
        "GRAYSCALE":   None,    # Skip — fund, not a token
        "CANTON":      "CC",
        "AXIOM":       None,    # Skip — no liquid token
    }

    # 2. Build symbol lookup for each top protocol
    # _key is already the best symbol from fees_data (could be "HYPERLIQUID", "ETH", etc.)
    # Map to CoinGecko symbol
    proto_to_symbol = {}
    for p in top_by_fees:
        key   = p.get("_key", "").upper()
        proto = p.get("protocol", key).upper()
        # Try direct mapping first
        cg_sym = PROTO_TO_CG_SYMBOL.get(proto) or PROTO_TO_CG_SYMBOL.get(key)
        if cg_sym is None and proto in PROTO_TO_CG_SYMBOL:
            continue  # explicitly skipped
        if cg_sym:
            proto_to_symbol[proto] = cg_sym
        elif key and len(key) <= 6 and key.isalpha():
            proto_to_symbol[proto] = key  # short symbol = use directly
        else:
            proto_to_symbol[proto] = None  # can't map

    # 3. Build CoinGecko map from top100 already fetched — no extra API call
    cg_map = {}
    if top100:
        for c in top100:
            sym = c.get("symbol", "").upper()
            cg_map[sym] = {
                "rank":        c.get("rank"),
                "price":       c.get("price"),
                "market_cap":  c.get("market_cap"),
                "change_7d_%": c.get("change_7d_%"),
                "change_24h_%": c.get("change_24h_%"),
            }

    # 4. Build enriched revenue leaders list — deduplicate by CG symbol
    result = []
    seen_cg_symbols = set()
    for p in top_by_fees:
        proto  = p.get("protocol", p.get("_key", "")).upper()
        cg_sym = proto_to_symbol.get(proto)

        if cg_sym is None:
            continue  # skip unmappable or explicitly excluded
        if cg_sym in seen_cg_symbols:
            # Merge fees (e.g. UNI appears from UNISWAP V2, V3, V4)
            for r in result:
                if r["symbol"] == cg_sym:
                    r["fees_1y"]  = (r.get("fees_1y")  or 0) + (p.get("fees_1y")  or 0) or None
                    r["fees_24h"] = (r.get("fees_24h") or 0) + (p.get("fees_24h") or 0) or None
            continue
        seen_cg_symbols.add(cg_sym)

        cg  = cg_map.get(cg_sym, {})
        tvl = tvl_data.get(cg_sym, {})
        result.append({
            "symbol":      cg_sym,
            "name":        p.get("protocol", cg_sym),
            "rank":        cg.get("rank"),
            "price":       cg.get("price"),
            "market_cap":  cg.get("market_cap"),
            "change_7d_%": cg.get("change_7d_%"),
            "fees_24h":    p.get("fees_24h"),
            "fees_7d":     p.get("fees_7d"),
            "fees_1y":     p.get("fees_1y"),
            "tvl":         tvl.get("tvl"),
            "tvl_7d_%":    tvl.get("tvl_7d_%"),
        })

    # Re-sort after merging versioned protocols
    result.sort(key=lambda x: x.get("fees_1y") or 0, reverse=True)
    result = result[:15]

    print(f"  → {len(result)} revenue leaders built from DeFiLlama")
    for r in result[:8]:
        print(f"    {r['symbol']:6s} fees_1y=${r.get('fees_1y') or 0:>12,.0f}  rank=#{r.get('rank')}")
    return result

def identify_movers(coins: list, revenue_leaders: list = None) -> dict:
    gainers_7d  = sorted([c for c in coins if c.get("change_7d_%") is not None],
                         key=lambda x: x["change_7d_%"], reverse=True)[:8]
    losers_7d   = sorted([c for c in coins if c.get("change_7d_%") is not None],
                         key=lambda x: x["change_7d_%"])[:5]
    near_ath    = [c for c in coins if (c.get("ath_ratio_%") or 0) >= 85]
    # Revenue leaders: use DeFiLlama-first list if available (Option 2)
    # This ensures we never miss high-revenue protocols regardless of market cap rank
    if revenue_leaders:
        high_fees = revenue_leaders[:15]
    else:
        # Fallback to old method
        with_fees = sorted(
            [c for c in coins if (c.get("fees_1y") or 0) > 100_000],
            key=lambda x: x.get("fees_1y") or 0, reverse=True
        )
        known_no_fees = [
            c for c in coins
            if c.get("symbol") in KNOWN_REVENUE
            and (c.get("fees_1y") or 0) <= 100_000
            and c not in with_fees
        ]
        known_no_fees.sort(key=lambda x: x.get("market_cap") or 0, reverse=True)
        high_fees = sorted(
            (with_fees + known_no_fees)[:15],
            key=lambda x: x.get("fees_1y") or x.get("fees_24h") or 0,
            reverse=True
        )
    return {
        "top_gainers_7d": gainers_7d,
        "top_losers_7d":  losers_7d,
        "near_ath":       near_ath,
        "highest_fees":   high_fees,
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


def save_memory(date: str, categories: dict, movers: dict, analysis: str):
    memory = load_memory()
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
            "fees_24h":     coin.get("fees_24h"),
            "tvl":          coin.get("tvl"),
        }
    memory[date] = {
        "signals":       {"categories": cat_signals, "notable_coins": notable},
        "brief_summary": analysis[:600],
    }
    cutoff = (datetime.now() - timedelta(days=MEMORY_DAYS)).strftime("%Y-%m-%d")
    memory = dict(sorted({k: v for k, v in memory.items() if k >= cutoff}.items()))
    with open(MEMORY_FILE, "w") as f:
        json.dump(memory, f, indent=2)
    print(f"💾 Crypto memory saved ({len(memory)} entries)")


def build_memory_context(memory: dict) -> str:
    if not memory:
        return "No historical data yet — this is the first run."
    lines = ["HISTORICAL CRYPTO SIGNAL TRACKER:"]
    all_cats = set()
    for d in memory.values():
        all_cats.update(d.get("signals", {}).get("categories", {}).keys())
    lines.append("── Category momentum (change_7d_%) ──")
    for cat in sorted(all_cats):
        row = f"{cat:20s}"
        for day, dd in sorted(memory.items())[-10:]:
            val = dd.get("signals", {}).get("categories", {}).get(cat, {}).get("change_7d_%")
            row += f"  {day[5:]}:{val:+.1f}%" if val is not None else "        —"
        lines.append(row)
    lines.append("\n── Notable coins on radar ──")
    coin_hist = {}
    for day, dd in sorted(memory.items()):
        for sym, data in dd.get("signals", {}).get("notable_coins", {}).items():
            coin_hist.setdefault(sym, []).append({"date": day, **data})
    for sym, appearances in sorted(coin_hist.items()):
        latest = appearances[-1]
        lines.append(
            f"{sym}: {len(appearances)}x | "
            f"7d={latest.get('change_7d_%', 0):+.1f}% "
            f"fees={latest.get('fees_24h') or '—'} "
            f"tvl={latest.get('tvl') or '—'}"
        )
    lines.append("\n── Recent summaries ──")
    for day, dd in sorted(memory.items())[-3:]:
        lines.append(f"\n{day}:\n{dd.get('brief_summary', '')[:200]}...")
    return "\n".join(lines)


# ── CLAUDE ANALYSIS ───────────────────────────────────────────────────────────

def analyze_with_claude(date: str, btc: dict, categories: dict,
                        filtered: dict, movers: dict, memory: dict) -> str:
    print("🤖 Running Claude crypto analysis...")
    client         = anthropic.Anthropic(api_key=ANTHROPIC_KEY)
    memory_context = build_memory_context(memory)

    prompt = f"""
You are a sharp crypto analyst identifying EARLY-STAGE trends and sector rotations in the top 100.
You focus on REVENUE-GENERATING projects (TVL + protocol fees) — not narrative-only plays.
RSI and MAs are computed on WEEKLY timeframes. Fibonacci on last major impulse (90-day window).
Analyze WEEKLY data in the context of monthly trends. Ignore daily noise.

Today is {date}.

═══════════════════════════════════════════
1. BTC — FULL TECHNICAL ANALYSIS
═══════════════════════════════════════════
{json.dumps(btc, indent=2)}

(Fibonacci levels above show last major impulse support/resistance zones)

═══════════════════════════════════════════
2. CATEGORY MOMENTUM
═══════════════════════════════════════════
{json.dumps(categories, indent=2)}

═══════════════════════════════════════════
3. REVENUE-GENERATING TOP 100 (TVL > $50M or fees > $100k/day)
═══════════════════════════════════════════
{json.dumps(filtered.get('revenue_generating', [])[:30], indent=2)}

═══════════════════════════════════════════
4. MEME COINS (separate, no revenue filter)
═══════════════════════════════════════════
{json.dumps(filtered.get('meme_coins', []), indent=2)}

═══════════════════════════════════════════
5. TOP MOVERS & FEES LEADERS
═══════════════════════════════════════════
{json.dumps(movers, indent=2)}

═══════════════════════════════════════════
6. HISTORICAL MEMORY
═══════════════════════════════════════════
{memory_context}

═══════════════════════════════════════════
YOUR BRIEF — 12 sections:
═══════════════════════════════════════════

IMPORTANT: If a data value is missing or null — OMIT that bullet point entirely. Do NOT write "N/A". Do NOT mention the data is unavailable. Just skip it silently. Exception: table cells use "—" if a specific column value is missing.

0. 🧠 EXECUTIVE SUMMARY (5 lines max — write this FIRST)
   The most important thing happening in crypto today. No fluff.
   Format: 5 bullet points, each one a complete standalone insight.
   Cover: BTC structure, top sector move, biggest risk, liquidity signal, one actionable idea.
   This section replaces the need to read everything else on a busy day.

1. ₿ BTC PULSE
   Output as 3 clearly separated sub-sections with a blank line between each.
   Use ## sub-headers for each sub-section so they render as distinct paragraphs.

   ## Price & Technicals
   - Price: $X | 1d: X% | 1w: X% | 1m: X%
   - Fibonacci: nearest support + resistance from last major impulse
   - RSI Weekly: only include if rsi_weekly is not null
   - MA Signal: only include if ma50_weekly or ma200_weekly is not null
   - If RSI/MA data is null, use price structure and daily RSI instead — note it

   ## 🌊 Global Liquidity
   - US M2: $XB | 1m: X% | 3m: X% | 12m: X% → EXPANDING or CONTRACTING
   - Fed Balance Sheet: X% (3m) / X% (12m) → QE or QT signal
   - **Verdict: TAILWIND / NEUTRAL / HEADWIND** — one sentence explanation

   ## 🔄 BTC Dominance & Rotation
   - Dominance: X% | 1w: +/-X% | 1m: +/-X%
   - ETH/BTC ratio trend + Total2 altcoin market cap
   - **Current Phase: PHASE X — [NAME]** — 2 sentences max explaining why
   - **Rotation Signal: NONE / EARLY / CONFIRMED / LATE**
   (Phases: 1=BTC Leads, 2=BTC Tops Out, 3=Alt Season, 4=Risk Off)

2. 🔥 TOP EMERGING TREND (1-2 categories)
   - Which category is rotating with REAL revenue behind it?
   - TVL growing? Fees growing? That's the signal.
   - Duration from memory + catalyst
   - Top 3 coins: symbol, rank, change_7d, fees_24h, tvl
   - Risk: LOW / MEDIUM / HIGH
   - 🎯 CONVICTION SCORE: X/10 — computed from:
       Signal duration (memory): 0-3 pts (1=new, 2=1wk+, 3=3wk+)
       Revenue backing:          0-2 pts (0=none, 1=TVL only, 2=fees+TVL)
       RSI zone:                 0-2 pts (0=overbought, 1=extended, 2=neutral/early)
       Volume confirmation:      0-2 pts (0=low, 1=normal, 2=spike)
       Macro alignment:          0-1 pt  (liquidity expanding = +1)
     Show breakdown: e.g. "7/10 (3+2+1+1+0)"

3. 📊 CATEGORY SCORECARD
   Output as markdown pipe table:
   | Category | 24h | 7d | Signal | Tag | Revenue? |
   |----------|-----|----|--------|-----|----------|
   | Layer 1 | +2.1% | +8.4% | HEATING UP 🔺 | CONFIRMED | Strong |
   Use change_24h_% and change_7d_% from category data.
   Signal: HEATING UP 🔺 / NEUTRAL ➡️ / COOLING DOWN 🔻
   Tag: NEW / CONFIRMED / FADING
   Revenue?: Strong / Speculative / Mixed

4. 💰 REVENUE LEADERS
   Take the highest_fees list from TOP MOVERS data. It is already sorted by fees_1y descending.
   DO NOT reorder — output rows in EXACTLY the order given in highest_fees.
   Output as markdown pipe table:
   | Symbol | Rank | Market Cap | Fees 1y | TVL | 7d Price |
   |--------|------|-----------|---------|-----|----------|
   - Flag ⚡ symbol when fees_1y is high but 7d price is negative = asymmetric opportunity
   - Max 15 rows, same order as data

5. 🎭 MEME WATCH
   Output as markdown pipe table:
   | Symbol | Rank | 24h | 7d | 30d | Vol 24h | Signal |
   |--------|------|-----|----|-----|---------|--------|
   Always include: DOGE, PEPE, BONK + top movers from the meme list.
   Pull trendy memes from rank 100-300 if showing unusual momentum.
   Signal column: 🔥 HOT / 📈 BUILDING / ➡️ SIDEWAYS / 🔻 FADING
   Then 2-line macro context: BTC dominance implication for meme season.
   No revenue commentary — purely price/momentum.

6. 😱 MARKET SENTIMENT & ON-CHAIN
   IMPORTANT: Only include a data point if its value is NOT null/None/empty.
   Skip entire bullet if data is unavailable — do NOT write "N/A".
   Always include these if available:
   - Fear & Greed: current value + label | vs 1w ago | vs 1m ago → GREEDY / FEARFUL / SHIFTING
   - Stablecoin supply: total + trend (GROWING = dry powder / SHRINKING = exiting)
   - ETF Flows: BTC ETF net flow today + 7d cumulative → institutional sentiment
   Only include if data is non-null:
   - Funding Rates BTC/ETH/SOL: avg rate + signal
   - Liquidations 24h: total + long vs short
   - Long/Short Ratio: % long / % short
   - Exchange Netflow BTC: inflow or outflow signal
   - BTC/SPX correlation: 30d + 90d (only if computed successfully)
   - BTC/Gold correlation: 30d + 90d (only if computed successfully)
   - **Verdict: HEALTHY / STRETCHED / DANGEROUS** — one sentence based on available data

7. 📅 UPCOMING CATALYSTS (max 4 bullets, one line each)
   Format: [Date] Event — Impact (BULLISH/BEARISH/NEUTRAL)
   Cover: macro events + crypto-specific. Flag uncertain dates.

8. 🔁 SIGNAL UPDATES (memory)
   - Follow up from previous days — confirmed / faded / still building

9. 💡 EARLY RADAR (max 2 setups, 3 lines each)
   Setup name + why interesting + one confirmation trigger.

10. ⚠️ KEY RISKS (max 3 bullets — be extremely concise)
    - One sentence per risk: what it is + potential impact
    - Prioritize: BTC technical > macro > regulatory

STRICT OUTPUT RULES:
- Complete ALL sections 0 through 10. Never skip or cut a section.
- If you are running long, write SHORTER bullets — do NOT omit sections.
- Section 6 (Market Sentiment) is frequently cut — it MUST appear in full.
- Executive Summary (section 0) is MANDATORY and must always be first.
- Never end mid-sentence. Never end mid-section.
- Use emojis and ## headers for each section so they render clearly.
- Target: 4-minute read. Achieve this by being concise, not by omitting sections.
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
        subject         = f"🪙 Crypto Weekly — {run_date}",
        header_title    = "🪙 Crypto Weekly Brief",
        header_color    = "#b35a00",
        bg_color        = "#fffdf0",
        accent          = "#b35a00",
        footer          = "Crypto Trend Bot · Not financial advice",
    )


# ── MAIN ──────────────────────────────────────────────────────────────────────

def main():
    print(f"\n🚀 Crypto Daily Bot starting — {datetime.now().strftime('%Y-%m-%d %H:%M')}")

    memory = load_memory()
    print(f"📚 Loaded {len(memory)} days of memory")

    today = datetime.now().strftime("%Y-%m-%d")

    # Fetch all data
    global_mkt  = fetch_global_market()
    time.sleep(3)
    liquidity   = fetch_global_liquidity()
    time.sleep(2)
    fear_greed  = fetch_fear_greed()
    time.sleep(2)
    funding     = fetch_funding_rates()
    time.sleep(2)
    stablecoins = fetch_stablecoin_supply()
    time.sleep(3)
    etf_flows   = fetch_etf_flows()
    time.sleep(2)
    netflow     = fetch_exchange_netflow()
    time.sleep(2)
    liquidations = fetch_liquidations()
    time.sleep(1)
    catalysts   = fetch_upcoming_catalysts()
    categories  = fetch_crypto_categories()
    time.sleep(8)
    top100      = fetch_crypto_top100()
    time.sleep(8)   # sleep after top100
    btc_ohlc    = fetch_btc_ohlc()
    time.sleep(8)
    tvl_data    = fetch_defillama_tvl()
    time.sleep(2)
    fees_data   = fetch_defillama_fees()
    time.sleep(8)
    correlations = fetch_correlations(top100)

    # Compute Total2 (altcoin market cap = total - BTC)
    if global_mkt.get("total_market_cap") and global_mkt.get("btc_dominance_%"):
        btc_mcap = global_mkt["total_market_cap"] * (global_mkt["btc_dominance_%"] / 100)
        global_mkt["altcoin_market_cap"] = global_mkt["total_market_cap"] - btc_mcap

    # Process
    categories = enrich_category_7d(categories, top100)
    revenue_leaders = fetch_top_revenue_leaders(fees_data, tvl_data, top100=top100)
    # No extra sleep needed — fetch_top_revenue_leaders uses top100 directly
    btc      = analyze_btc(top100, btc_ohlc)
    btc["global_market"] = global_mkt
    btc["liquidity"]     = liquidity
    btc["fear_greed"]    = fear_greed
    btc["funding"]       = funding
    btc["stablecoins"]   = stablecoins
    btc["etf_flows"]     = etf_flows
    btc["netflow"]       = netflow
    btc["liquidations"]  = liquidations
    btc["correlations"]  = correlations
    btc["catalysts"]     = catalysts
    filtered = enrich_and_filter(top100, tvl_data, fees_data, categories)
    movers   = identify_movers(
        filtered["revenue_generating"] + filtered["meme_coins"],
        revenue_leaders=revenue_leaders
    )

    print(f"  → {len(filtered['revenue_generating'])} revenue-generating coins")
    print(f"  → {len(filtered['meme_coins'])} meme coins")
    print(f"  → {filtered['filtered_out_count']} filtered out (no revenue, not meme)")

    analysis = analyze_with_claude(today, btc, categories, filtered, movers, memory)

    save_memory(today, categories, movers, analysis)
    send_email(analysis, today)

    print("\n✅ Done!\n")
    print("─" * 60)
    print(analysis)


if __name__ == "__main__":
    main()