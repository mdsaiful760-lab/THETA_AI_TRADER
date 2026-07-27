import os
from datetime import date, datetime
from dotenv import load_dotenv
from kiteconnect import KiteConnect

from greeks import implied_volatility, calculate_greeks


# ============================================================
# CONFIGURATION
# ============================================================

TARGET_DELTA = 0.15
MIN_DELTA = 0.10
MAX_DELTA = 0.20

RISK_FREE_RATE = 0.06

# Safety switch
ORDER_PLACEMENT_ENABLED = False


# ============================================================
# KITE CONNECTION
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
print("🚀 THETA AI TRADER — LIVE SIGNAL ENGINE")
print("=" * 70)


# ============================================================
# GET NIFTY SPOT
# ============================================================

spot_data = kite.ltp("NSE:NIFTY 50")
spot = float(
    spot_data["NSE:NIFTY 50"]["last_price"]
)

print(f"\n📊 NIFTY SPOT : {spot:.2f}")


# ============================================================
# GET NFO INSTRUMENTS
# ============================================================

print("📥 Loading NIFTY instruments...")

instruments = kite.instruments("NFO")

nifty_options = [
    instrument
    for instrument in instruments
    if instrument["name"] == "NIFTY"
    and instrument["instrument_type"] in ("CE", "PE")
]


if not nifty_options:
    raise RuntimeError("No NIFTY options found.")


# ============================================================
# FIND NEAREST EXPIRY
# ============================================================

today = date.today()

expiries = sorted({
    instrument["expiry"]
    for instrument in nifty_options
    if instrument["expiry"] >= today
})

if not expiries:
    raise RuntimeError("No future NIFTY expiry found.")

expiry = expiries[0]

print(f"📅 EXPIRY     : {expiry}")


# ============================================================
# FILTER EXPIRY
# ============================================================

expiry_options = [
    instrument
    for instrument in nifty_options
    if instrument["expiry"] == expiry
]


strikes = sorted({
    float(instrument["strike"])
    for instrument in expiry_options
})


atm = min(
    strikes,
    key=lambda strike: abs(strike - spot)
)

print(f"🎯 ATM STRIKE : {atm:.0f}")


# ============================================================
# SELECT STRIKES AROUND ATM
# ============================================================

nearby_options = [
    instrument
    for instrument in expiry_options
    if abs(float(instrument["strike"]) - atm) <= 1000
]


symbols = [
    f'NFO:{instrument["tradingsymbol"]}'
    for instrument in nearby_options
]


print("📡 Fetching live option prices...")

quotes = kite.quote(symbols)


# ============================================================
# TIME TO EXPIRY
# ============================================================

now = datetime.now()

expiry_datetime = datetime.combine(
    expiry,
    datetime.strptime("15:30", "%H:%M").time()
)

seconds_remaining = (
    expiry_datetime - now
).total_seconds()

# Prevent zero/negative time
seconds_remaining = max(seconds_remaining, 60)

time_to_expiry = seconds_remaining / (
    365 * 24 * 60 * 60
)


# ============================================================
# CALCULATE GREEKS
# ============================================================

ce_candidates = []
pe_candidates = []


print("🧮 Calculating Greeks...")


for instrument in nearby_options:

    symbol = f'NFO:{instrument["tradingsymbol"]}'

    quote = quotes.get(symbol)

    if not quote:
        continue

    premium = float(
        quote.get("last_price", 0) or 0
    )

    if premium <= 0:
        continue

    strike = float(instrument["strike"])
    option_type = instrument["instrument_type"]

    try:

        iv = implied_volatility(
            premium,
            spot,
            strike,
            time_to_expiry,
            RISK_FREE_RATE,
            option_type
        )

        if iv is None or iv <= 0:
            continue

        greeks = calculate_greeks(
            spot,
            strike,
            time_to_expiry,
            RISK_FREE_RATE,
            iv,
            option_type
        )

        delta = float(greeks["delta"])

        candidate = {
            "strike": strike,
            "premium": premium,
            "delta": delta,
            "iv": iv,
            "oi": quote.get("oi", 0),
            "symbol": instrument["tradingsymbol"]
        }


        if option_type == "CE":

            if MIN_DELTA <= delta <= MAX_DELTA:
                ce_candidates.append(candidate)


        elif option_type == "PE":

            if MIN_DELTA <= abs(delta) <= MAX_DELTA:
                pe_candidates.append(candidate)

    except Exception:
        continue


# ============================================================
# FIND CLOSEST TO TARGET DELTA
# ============================================================

best_ce = None
best_pe = None


if ce_candidates:

    best_ce = min(
        ce_candidates,
        key=lambda x: abs(
            x["delta"] - TARGET_DELTA
        )
    )


if pe_candidates:

    best_pe = min(
        pe_candidates,
        key=lambda x: abs(
            abs(x["delta"]) - TARGET_DELTA
        )
    )


# ============================================================
# DISPLAY SIGNAL
# ============================================================

print("\n" + "=" * 70)

print("🤖 THETA AI TRADER — LIVE STRANGLE SIGNAL")

print("=" * 70)


if best_ce:

    print("\n🔴 CE SELL CANDIDATE")
    print(f"Symbol  : {best_ce['symbol']}")
    print(f"Strike  : {best_ce['strike']:.0f}")
    print(f"Delta   : {best_ce['delta']:.3f}")
    print(f"Premium : {best_ce['premium']:.2f}")
    print(f"IV      : {best_ce['iv']:.2%}")
    print(f"OI      : {best_ce['oi']}")

else:

    print("\n❌ No suitable CE found.")


if best_pe:

    print("\n🟢 PE SELL CANDIDATE")
    print(f"Symbol  : {best_pe['symbol']}")
    print(f"Strike  : {best_pe['strike']:.0f}")
    print(f"Delta   : {best_pe['delta']:.3f}")
    print(f"Premium : {best_pe['premium']:.2f}")
    print(f"IV      : {best_pe['iv']:.2%}")
    print(f"OI      : {best_pe['oi']}")

else:

    print("\n❌ No suitable PE found.")


# ============================================================
# SAFETY
# ============================================================

print("\n" + "=" * 70)

if ORDER_PLACEMENT_ENABLED:

    print("⚠️ ORDER PLACEMENT SWITCH IS ENABLED")

else:

    print("🔒 SIGNAL ONLY")
    print("❌ NO ORDERS CAN BE PLACED")

print("=" * 70)