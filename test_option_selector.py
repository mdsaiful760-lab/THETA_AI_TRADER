import os

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from option_selector import OptionSelector


# ============================================================
# KITE CONNECTION
# ============================================================

load_dotenv()

api_key = os.getenv("KITE_API_KEY")
access_token = os.getenv("KITE_ACCESS_TOKEN")

if not api_key or not access_token:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing"
    )

kite = KiteConnect(
    api_key=api_key
)

kite.set_access_token(
    access_token
)


# ============================================================
# DOWNLOAD NFO INSTRUMENT MASTER
# ============================================================

print("=" * 60)
print("🔎 THETA AI TRADER — OPTION DISCOVERY TEST")
print("=" * 60)

print("Downloading NFO instruments...")

instruments = kite.instruments("NFO")

print(
    f"Total NFO instruments : "
    f"{len(instruments)}"
)


# ============================================================
# OPTION SELECTOR
# ============================================================

selector = OptionSelector()

nifty_options = (
    selector.filter_nifty_options(
        instruments
    )
)

expiries = (
    selector.get_available_expiries(
        nifty_options
    )
)

nearest_expiry = (
    selector.select_nearest_expiry(
        nifty_options
    )
)

contracts = (
    selector.contracts_for_expiry(
        nifty_options,
        nearest_expiry,
    )
)

# ============================================================
# LIVE NIFTY SPOT
# ============================================================

spot_data = kite.ltp(
    "NSE:NIFTY 50"
)

spot_price = float(
    spot_data["NSE:NIFTY 50"]["last_price"]
)


# ============================================================
# STRIKE WINDOW
# ============================================================

strike_window = (
    selector.select_strike_window(
        contracts,
        spot_price,
        points=500,
    )
)


# ============================================================
# LIVE OPTION QUOTES
# ============================================================

quote_symbols = (
    selector.build_quote_symbols(
        strike_window
    )
)

option_quotes = kite.quote(
    quote_symbols
)

live_options = (
    selector.process_quotes(
        strike_window,
        option_quotes,
    )
)

# ============================================================
# LIQUIDITY FILTER
# ============================================================

liquid_options = (
    selector.filter_liquid_options(
        live_options,
        min_volume=100000,
        min_oi=50000,
        max_spread_pct=5.0,
    )
)

# ============================================================
# OUTPUT
# ============================================================

print(
    f"NIFTY option contracts: "
    f"{len(nifty_options)}"
)

print(
    f"Active expiries        : "
    f"{len(expiries)}"
)

print(
    f"Nearest expiry         : "
    f"{nearest_expiry}"
)

print(
    f"Contracts in expiry    : "
    f"{len(contracts)}"
)

print(
    f"NIFTY Spot             : "
    f"{spot_price:.2f}"
)

print(
    f"Contracts ±500 points  : "
    f"{len(strike_window)}"
)

print("\nNext available expiries:")

for expiry in expiries[:5]:
    print(f"• {expiry}")


# ============================================================
# SAMPLE CONTRACTS AROUND LIST CENTRE
# ============================================================

print("\n" + "=" * 60)
print("📋 SAMPLE CONTRACTS")
print("=" * 60)

middle = len(contracts) // 2

start = max(
    0,
    middle - 3,
)

end = min(
    len(contracts),
    middle + 3,
)

for contract in contracts[start:end]:

    print(
        f"{contract['tradingsymbol']} | "
        f"Strike {contract['strike']:.0f} | "
        f"{contract['option_type']} | "
        f"Expiry {contract['expiry']}"
    )

print("\n" + "=" * 60)
print("🎯 SPOT-BASED STRIKE WINDOW")
print("=" * 60)

for contract in strike_window:

    distance = (
        float(contract["strike"])
        - spot_price
    )

    print(
        f"{contract['tradingsymbol']} | "
        f"{contract['strike']:.0f} "
        f"{contract['option_type']} | "
        f"Distance {distance:+.2f}"
    )

print("\n" + "=" * 60)
print("📡 LIVE OPTION MARKET DATA")
print("=" * 60)

for option in live_options:

    spread_pct = option["spread_pct"]

    if spread_pct is None:
        spread_text = "N/A"
    else:
        spread_text = f"{spread_pct:.2f}%"

    print(
        f"{option['strike']:.0f} "
        f"{option['option_type']} | "
        f"LTP {option['ltp']:.2f} | "
        f"Bid {option['best_bid']:.2f} | "
        f"Ask {option['best_ask']:.2f} | "
        f"Spread {spread_text} | "
        f"Vol {option['volume']} | "
        f"OI {option['oi']}"
    )

print("\n" + "=" * 60)
print("💧 LIQUIDITY FILTER RESULT")
print("=" * 60)

print(
    f"Contracts Before Filter : {len(live_options)}"
)

print(
    f"Contracts After Filter  : {len(liquid_options)}"
)

print(
    f"Contracts Rejected      : "
    f"{len(live_options) - len(liquid_options)}"
)

print("\nQUALIFIED CONTRACTS:")

for option in liquid_options:

    spread_pct = option["spread_pct"]

    print(
        f"{option['strike']:.0f} "
        f"{option['option_type']} | "
        f"LTP {option['ltp']:.2f} | "
        f"Spread {spread_pct:.2f}% | "
        f"Vol {option['volume']} | "
        f"OI {option['oi']}"
    )

print("\nSelector Reasons:")

for reason in selector.reasons:
    print(f"• {reason}")

print("=" * 60)