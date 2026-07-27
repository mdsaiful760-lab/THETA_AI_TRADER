import os
from datetime import date
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# --------------------------------------------------
# LOAD CREDENTIALS
# --------------------------------------------------

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

if not API_KEY or not ACCESS_TOKEN:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing from .env"
    )

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)


# --------------------------------------------------
# GET NIFTY SPOT
# --------------------------------------------------

quote = kite.ltp("NSE:NIFTY 50")
spot = quote["NSE:NIFTY 50"]["last_price"]

print("=" * 70)
print("🚀 THETA AI TRADER — NIFTY OPTION CHAIN")
print("=" * 70)

print(f"\nNIFTY SPOT : {spot}")


# --------------------------------------------------
# DOWNLOAD NFO INSTRUMENTS
# --------------------------------------------------

print("\nDownloading NFO instruments...")

instruments = kite.instruments("NFO")


# --------------------------------------------------
# FILTER NIFTY OPTIONS
# --------------------------------------------------

nifty_options = [
    i for i in instruments
    if i["name"] == "NIFTY"
    and i["instrument_type"] in ("CE", "PE")
]


if not nifty_options:
    raise RuntimeError("No NIFTY options found.")


# --------------------------------------------------
# FIND NEAREST AVAILABLE EXPIRY
# --------------------------------------------------

today = date.today()

expiries = sorted({
    i["expiry"]
    for i in nifty_options
    if i["expiry"] >= today
})

if not expiries:
    raise RuntimeError("No future NIFTY expiry found.")

nearest_expiry = expiries[0]

print(f"Nearest Expiry : {nearest_expiry}")


# --------------------------------------------------
# FILTER NEAREST EXPIRY
# --------------------------------------------------

expiry_options = [
    i for i in nifty_options
    if i["expiry"] == nearest_expiry
]


# --------------------------------------------------
# DETECT AVAILABLE STRIKE INTERVAL
# --------------------------------------------------

strikes = sorted({
    float(i["strike"])
    for i in expiry_options
})

if len(strikes) < 2:
    raise RuntimeError("Not enough strikes found.")

strike_steps = [
    strikes[i + 1] - strikes[i]
    for i in range(len(strikes) - 1)
    if strikes[i + 1] > strikes[i]
]

strike_step = min(strike_steps)

atm = min(strikes, key=lambda strike: abs(strike - spot))

print(f"ATM Strike     : {atm:.0f}")
print(f"Strike Step    : {strike_step:.0f}")


# --------------------------------------------------
# SELECT ±10 STRIKES AROUND ATM
# --------------------------------------------------

nearby_strikes = [
    strike
    for strike in strikes
    if atm - (10 * strike_step)
    <= strike
    <= atm + (10 * strike_step)
]


# --------------------------------------------------
# CREATE CE / PE LOOKUP
# --------------------------------------------------

option_map = {}

for instrument in expiry_options:

    strike = float(instrument["strike"])

    if strike not in nearby_strikes:
        continue

    option_map.setdefault(strike, {})

    option_map[strike][instrument["instrument_type"]] = instrument


# --------------------------------------------------
# FETCH QUOTES
# --------------------------------------------------

symbols = []

for strike in nearby_strikes:

    legs = option_map.get(strike, {})

    for option_type in ("CE", "PE"):

        instrument = legs.get(option_type)

        if instrument:
            symbols.append(
                f'NFO:{instrument["tradingsymbol"]}'
            )


quotes = kite.quote(symbols) if symbols else {}


# --------------------------------------------------
# DISPLAY OPTION CHAIN
# --------------------------------------------------

print("\n" + "=" * 90)

print(
    f"{'STRIKE':<10}"
    f"{'CE LTP':>12}"
    f"{'CE OI':>15}"
    f"{'PE LTP':>15}"
    f"{'PE OI':>15}"
)

print("-" * 90)


for strike in nearby_strikes:

    legs = option_map.get(strike, {})

    ce = legs.get("CE")
    pe = legs.get("PE")

    ce_ltp = "-"
    ce_oi = "-"

    pe_ltp = "-"
    pe_oi = "-"

    if ce:
        key = f'NFO:{ce["tradingsymbol"]}'
        q = quotes.get(key, {})

        ce_ltp = q.get("last_price", "-")
        ce_oi = q.get("oi", "-")

    if pe:
        key = f'NFO:{pe["tradingsymbol"]}'
        q = quotes.get(key, {})

        pe_ltp = q.get("last_price", "-")
        pe_oi = q.get("oi", "-")

    atm_marker = " <-- ATM" if strike == atm else ""

    print(
        f"{strike:<10.0f}"
        f"{str(ce_ltp):>12}"
        f"{str(ce_oi):>15}"
        f"{str(pe_ltp):>15}"
        f"{str(pe_oi):>15}"
        f"{atm_marker}"
    )


print("=" * 90)

print("\n✅ OPTION CHAIN LOADED SUCCESSFULLY")
print("🔒 READ-ONLY MODE — NO ORDERS CAN BE PLACED")