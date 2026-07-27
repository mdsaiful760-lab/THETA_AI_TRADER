import os
from datetime import date
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# ============================================================
# THETA AI TRADER - LIVE OPTION ANALYSIS
# READ-ONLY VERSION
# ============================================================

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

if not API_KEY or not ACCESS_TOKEN:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing from .env"
    )

# Connect to Kite
kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

print("=" * 80)
print("🚀 THETA AI TRADER — LIVE OPTION ANALYSIS")
print("=" * 80)


# ============================================================
# 1. GET NIFTY SPOT
# ============================================================

try:
    spot_data = kite.ltp("NSE:NIFTY 50")
    spot = spot_data["NSE:NIFTY 50"]["last_price"]

    print(f"\n📊 NIFTY SPOT : {spot:.2f}")

except Exception as e:
    print("\n❌ Could not get NIFTY spot price.")
    print("Error:", e)
    raise


# ============================================================
# 2. FIND ATM STRIKE
# ============================================================

strike_interval = 50

atm = round(spot / strike_interval) * strike_interval

print(f"🎯 ATM STRIKE : {atm}")


# ============================================================
# 3. DOWNLOAD NFO INSTRUMENTS
# ============================================================

print("\n⏳ Loading NFO instruments...")

try:
    instruments = kite.instruments("NFO")

except Exception as e:
    print("❌ Unable to download NFO instruments.")
    print("Error:", e)
    raise


# ============================================================
# 4. FILTER NIFTY OPTIONS
# ============================================================

today = date.today()

nifty_options = []

for instrument in instruments:

    if (
        instrument.get("name") == "NIFTY"
        and instrument.get("instrument_type") in ["CE", "PE"]
        and instrument.get("expiry")
        and instrument["expiry"] >= today
    ):
        nifty_options.append(instrument)


if not nifty_options:
    raise ValueError("No valid NIFTY option contracts found.")


# ============================================================
# 5. FIND NEAREST AVAILABLE EXPIRY
# ============================================================

expiries = sorted(
    set(instrument["expiry"] for instrument in nifty_options)
)

nearest_expiry = expiries[0]

print(f"📅 NEAREST EXPIRY : {nearest_expiry}")


# Keep only nearest expiry
expiry_options = [
    instrument
    for instrument in nifty_options
    if instrument["expiry"] == nearest_expiry
]


# ============================================================
# 6. CREATE STRIKE RANGE
# ============================================================

strikes = [
    atm + (i * strike_interval)
    for i in range(-10, 11)
]

print(
    f"📈 STRIKE RANGE : "
    f"{strikes[0]} - {strikes[-1]}"
)


# ============================================================
# 7. BUILD CE / PE CONTRACT MAP
# ============================================================

contracts = {}

for instrument in expiry_options:

    strike = int(instrument["strike"])

    if strike not in strikes:
        continue

    if strike not in contracts:
        contracts[strike] = {}

    contracts[strike][instrument["instrument_type"]] = instrument


# ============================================================
# 8. PREPARE QUOTE SYMBOLS
# ============================================================

quote_symbols = []

for strike in strikes:

    contract = contracts.get(strike, {})

    ce = contract.get("CE")
    pe = contract.get("PE")

    if ce:
        quote_symbols.append(
            "NFO:" + ce["tradingsymbol"]
        )

    if pe:
        quote_symbols.append(
            "NFO:" + pe["tradingsymbol"]
        )


if not quote_symbols:
    raise ValueError(
        "No option symbols found for selected strikes."
    )


# ============================================================
# 9. GET LIVE OPTION QUOTES
# ============================================================

print("\n⏳ Fetching live option quotes...\n")

try:
    quotes = kite.quote(quote_symbols)

except Exception as e:
    print("❌ Unable to fetch option quotes.")
    print("Error:", e)
    raise


# ============================================================
# 10. DISPLAY OPTION CHAIN
# ============================================================

print("=" * 80)

print(
    f"{'STRIKE':<10}"
    f"{'CE LTP':<12}"
    f"{'CE OI':<15}"
    f"{'PE LTP':<12}"
    f"{'PE OI':<15}"
)

print("-" * 80)


for strike in strikes:

    contract = contracts.get(strike, {})

    ce = contract.get("CE")
    pe = contract.get("PE")

    ce_ltp = "-"
    ce_oi = "-"

    pe_ltp = "-"
    pe_oi = "-"


    # CE DATA
    if ce:

        symbol = "NFO:" + ce["tradingsymbol"]

        data = quotes.get(symbol, {})

        ce_ltp = data.get("last_price", "-")
        ce_oi = data.get("oi", "-")


    # PE DATA
    if pe:

        symbol = "NFO:" + pe["tradingsymbol"]

        data = quotes.get(symbol, {})

        pe_ltp = data.get("last_price", "-")
        pe_oi = data.get("oi", "-")


    atm_marker = " <-- ATM" if strike == atm else ""

    print(
        f"{strike:<10}"
        f"{str(ce_ltp):<12}"
        f"{str(ce_oi):<15}"
        f"{str(pe_ltp):<12}"
        f"{str(pe_oi):<15}"
        f"{atm_marker}"
    )


print("=" * 80)

print("\n✅ LIVE OPTION CHAIN LOADED")
print(f"📊 NIFTY : {spot:.2f}")
print(f"🎯 ATM   : {atm}")
print(f"📅 EXPIRY: {nearest_expiry}")

print("\n🔒 READ-ONLY MODE")
print("❌ NO ORDERS CAN BE PLACED")

print("=" * 80)