import os
from datetime import date

from dotenv import load_dotenv
from kiteconnect import KiteConnect


# ============================================================
# THETA AI TRADER — MARKET ANALYZER
# READ-ONLY — NO ORDER PLACEMENT
# ============================================================

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

if not API_KEY or not ACCESS_TOKEN:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing from .env"
    )

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)


print("=" * 70)
print("🚀 THETA AI TRADER — MARKET ANALYZER")
print("=" * 70)


# ------------------------------------------------------------
# 1. GET NIFTY SPOT
# ------------------------------------------------------------

quote = kite.ltp("NSE:NIFTY 50")
spot = quote["NSE:NIFTY 50"]["last_price"]

print(f"\n📊 NIFTY SPOT : {spot}")


# ------------------------------------------------------------
# 2. LOAD NIFTY OPTIONS
# ------------------------------------------------------------

print("📥 Loading NIFTY instruments...")

instruments = kite.instruments("NFO")

nifty_options = [
    i for i in instruments
    if i["name"] == "NIFTY"
    and i["instrument_type"] in ("CE", "PE")
]

if not nifty_options:
    raise RuntimeError("No NIFTY options found.")


# ------------------------------------------------------------
# 3. FIND NEAREST EXPIRY
# ------------------------------------------------------------

today = date.today()

expiries = sorted({
    i["expiry"]
    for i in nifty_options
    if i["expiry"] >= today
})

if not expiries:
    raise RuntimeError("No future NIFTY expiry found.")

expiry = expiries[0]

print(f"📅 EXPIRY     : {expiry}")


expiry_options = [
    i for i in nifty_options
    if i["expiry"] == expiry
]


# ------------------------------------------------------------
# 4. FIND ATM + STRIKE STEP
# ------------------------------------------------------------

strikes = sorted({
    float(i["strike"])
    for i in expiry_options
})

if len(strikes) < 2:
    raise RuntimeError("Not enough strikes found.")

steps = [
    strikes[i + 1] - strikes[i]
    for i in range(len(strikes) - 1)
    if strikes[i + 1] > strikes[i]
]

strike_step = min(steps)

atm = min(
    strikes,
    key=lambda strike: abs(strike - spot)
)

print(f"🎯 ATM STRIKE : {atm:.0f}")


# ------------------------------------------------------------
# 5. ANALYSIS RANGE
# ±10 strikes around ATM
# ------------------------------------------------------------

analysis_strikes = [
    strike
    for strike in strikes
    if atm - (10 * strike_step)
    <= strike
    <= atm + (10 * strike_step)
]

print(
    f"🔎 RANGE      : "
    f"{analysis_strikes[0]:.0f} - "
    f"{analysis_strikes[-1]:.0f}"
)


# ------------------------------------------------------------
# 6. BUILD OPTION LOOKUP
# ------------------------------------------------------------

option_map = {}

for instrument in expiry_options:

    strike = float(instrument["strike"])

    if strike not in analysis_strikes:
        continue

    option_map.setdefault(strike, {})

    option_map[strike][
        instrument["instrument_type"]
    ] = instrument


# ------------------------------------------------------------
# 7. FETCH LIVE QUOTES
# ------------------------------------------------------------

symbols = []

for strike in analysis_strikes:

    legs = option_map.get(strike, {})

    for option_type in ("CE", "PE"):

        instrument = legs.get(option_type)

        if instrument:

            symbols.append(
                f'NFO:{instrument["tradingsymbol"]}'
            )


print("📡 Fetching live option OI...")

quotes = kite.quote(symbols) if symbols else {}


# ------------------------------------------------------------
# 8. COLLECT OI DATA
# ------------------------------------------------------------

chain = []

total_ce_oi = 0
total_pe_oi = 0


for strike in analysis_strikes:

    legs = option_map.get(strike, {})

    ce = legs.get("CE")
    pe = legs.get("PE")

    ce_oi = 0
    pe_oi = 0

    ce_ltp = 0
    pe_ltp = 0


    if ce:

        key = f'NFO:{ce["tradingsymbol"]}'

        q = quotes.get(key, {})

        ce_oi = q.get("oi", 0) or 0
        ce_ltp = q.get("last_price", 0) or 0


    if pe:

        key = f'NFO:{pe["tradingsymbol"]}'

        q = quotes.get(key, {})

        pe_oi = q.get("oi", 0) or 0
        pe_ltp = q.get("last_price", 0) or 0


    total_ce_oi += ce_oi
    total_pe_oi += pe_oi


    chain.append({
        "strike": strike,
        "ce_oi": ce_oi,
        "pe_oi": pe_oi,
        "ce_ltp": ce_ltp,
        "pe_ltp": pe_ltp
    })


# ------------------------------------------------------------
# 9. CALCULATE PCR
# ------------------------------------------------------------

if total_ce_oi > 0:

    pcr = total_pe_oi / total_ce_oi

else:

    pcr = 0


# ------------------------------------------------------------
# 10. FIND MAJOR RESISTANCE
# Highest CE OI
# ------------------------------------------------------------

resistance_row = max(
    chain,
    key=lambda x: x["ce_oi"]
)

resistance = resistance_row["strike"]


# ------------------------------------------------------------
# 11. FIND MAJOR SUPPORT
# Highest PE OI
# ------------------------------------------------------------

support_row = max(
    chain,
    key=lambda x: x["pe_oi"]
)

support = support_row["strike"]


# ------------------------------------------------------------
# 12. DETERMINE BASIC MARKET BIAS
# ------------------------------------------------------------

if pcr >= 1.20:

    bias = "BULLISH 🟢"

elif pcr <= 0.80:

    bias = "BEARISH 🔴"

else:

    bias = "NEUTRAL / RANGE BOUND 🟡"


# ------------------------------------------------------------
# 13. STRANGLE ENVIRONMENT CHECK
# ------------------------------------------------------------

range_width = resistance - support

distance_to_support = spot - support
distance_to_resistance = resistance - spot


if (
    0.80 <= pcr <= 1.20
    and support < spot < resistance
):

    strangle_status = "FAVOURABLE 🟢"

else:

    strangle_status = "CAUTION 🟡"


# ------------------------------------------------------------
# 14. DISPLAY RESULTS
# ------------------------------------------------------------

print("\n" + "=" * 70)

print("🤖 THETA AI TRADER — MARKET STRUCTURE")

print("=" * 70)


print(f"""

NIFTY SPOT
-----------
Spot            : {spot:.2f}
ATM             : {atm:.0f}


OPTION OI
---------
Total CE OI     : {total_ce_oi:,}
Total PE OI     : {total_pe_oi:,}

PCR             : {pcr:.2f}


MARKET LEVELS
-------------
🟢 Support      : {support:.0f}
🔴 Resistance   : {resistance:.0f}

Range Width     : {range_width:.0f} points

Distance Support    : {distance_to_support:.2f}
Distance Resistance : {distance_to_resistance:.2f}


MARKET BIAS
-----------
{bias}


SHORT STRANGLE ENVIRONMENT
--------------------------
{strangle_status}

""")


# ------------------------------------------------------------
# 15. TOP OI LEVELS
# ------------------------------------------------------------

print("=" * 70)
print("📊 TOP OPTION OI LEVELS")
print("=" * 70)


top_ce = sorted(
    chain,
    key=lambda x: x["ce_oi"],
    reverse=True
)[:3]


top_pe = sorted(
    chain,
    key=lambda x: x["pe_oi"],
    reverse=True
)[:3]


print("\n🔴 TOP CE OI — RESISTANCE")

for row in top_ce:

    print(
        f"{row['strike']:.0f} CE"
        f" | OI: {row['ce_oi']:,}"
    )


print("\n🟢 TOP PE OI — SUPPORT")

for row in top_pe:

    print(
        f"{row['strike']:.0f} PE"
        f" | OI: {row['pe_oi']:,}"
    )


print("\n" + "=" * 70)

print("🔒 ANALYSIS ONLY — NO ORDER PLACEMENT")

print("=" * 70)