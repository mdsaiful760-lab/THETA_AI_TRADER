import os

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from option_selector import OptionSelector
from greeks_engine import GreeksEngine


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

greeks_engine = GreeksEngine(
    risk_free_rate=0.06
)

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
        points=1000,
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
        max_spread_pct=6.0,
    )
)

# ============================================================
# GREEKS-ENRICHED OPTION CHAIN
# ============================================================

greeks_options = (
    selector.enrich_with_greeks(
        options=liquid_options,
        spot_price=spot_price,
        greeks_engine=greeks_engine,
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
    f"Contracts ±1000 points  : "
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

# ============================================================
# LIVE GREEKS — SINGLE CONTRACT TEST
# ============================================================

if not liquid_options:
    raise ValueError(
        "No liquid options available for Greeks test"
    )

test_contract = liquid_options[0]

live_time_to_expiry = (
    greeks_engine.time_to_expiry(
        test_contract["expiry"]
    )
)

print("\n" + "=" * 60)
print("🧮 LIVE GREEKS — SINGLE CONTRACT TEST")
print("=" * 60)

print(
    f"Contract          : "
    f"{test_contract['tradingsymbol']}"
)

print(
    f"Strike            : "
    f"{test_contract['strike']:.0f}"
)

print(
    f"Option Type       : "
    f"{test_contract['option_type']}"
)

print(
    f"Market Price      : "
    f"{test_contract['ltp']:.2f}"
)

print(
    f"Time to Expiry    : "
    f"{live_time_to_expiry:.8f} years"
)

print("=" * 60)

# ============================================================
# LIVE IMPLIED VOLATILITY
# ============================================================

live_iv = greeks_engine.implied_volatility(
    market_price=test_contract["ltp"],
    spot=spot_price,
    strike=test_contract["strike"],
    time_to_expiry=live_time_to_expiry,
    option_type=test_contract["option_type"],
)

print(
    f"Live Implied Vol : "
    f"{live_iv * 100:.2f}%"
)

# ============================================================
# LIVE OPTION GREEKS
# ============================================================

live_greeks = greeks_engine.calculate_greeks(
    spot=spot_price,
    strike=test_contract["strike"],
    time_to_expiry=live_time_to_expiry,
    volatility=live_iv,
    option_type=test_contract["option_type"],
)

print("-" * 60)

print(
    f"Delta             : "
    f"{live_greeks['delta']:.4f}"
)

print(
    f"Gamma             : "
    f"{live_greeks['gamma']:.8f}"
)

print(
    f"Theta / Day       : "
    f"{live_greeks['theta']:.4f}"
)

print(
    f"Vega / 1% IV      : "
    f"{live_greeks['vega']:.4f}"
)

print("=" * 60)

# ============================================================
# LIVE GREEKS-ENRICHED OPTION CHAIN
# ============================================================

print("\n" + "=" * 80)
print("🧮 LIVE GREEKS-ENRICHED OPTION CHAIN")
print("=" * 80)

print(
    f"{'STRIKE':<8} "
    f"{'TYPE':<6} "
    f"{'LTP':<10} "
    f"{'IV':<10} "
    f"{'DELTA':<10} "
    f"{'THETA':<12} "
    f"{'VEGA':<10}"
)

print("-" * 80)

for option in greeks_options:

    print(
        f"{option['strike']:<8.0f} "
        f"{option['option_type']:<6} "
        f"{option['ltp']:<10.2f} "
        f"{option['implied_volatility'] * 100:<10.2f} "
        f"{option['delta']:<10.4f} "
        f"{option['theta']:<12.4f} "
        f"{option['vega']:<10.4f}"
    )

print("-" * 80)

print(
    f"Greeks Contracts : "
    f"{len(greeks_options)}"
)

print("=" * 80)


# ============================================================
# TARGET-DELTA STRIKE SELECTION TEST
# ============================================================

print("\n" + "=" * 80)
print("🎯 TARGET-DELTA STRIKE SELECTION")
print("=" * 80)

selected_ce = selector.select_by_target_delta(
    options=greeks_options,
    spot_price=spot_price,
    option_type="CE",
    target_delta=0.15,
    max_delta_difference=0.05,
)

selected_pe = selector.select_by_target_delta(
    options=greeks_options,
    spot_price=spot_price,
    option_type="PE",
    target_delta=-0.15,
    max_delta_difference=0.05,
)

if selected_ce:

    print("\nSELECTED CALL:")

    print(
        f"Contract : {selected_ce['tradingsymbol']}"
    )

    print(
        f"Strike   : {selected_ce['strike']:.0f} CE"
    )

    print(
        f"LTP      : {selected_ce['ltp']:.2f}"
    )

    print(
        f"Delta    : {selected_ce['delta']:+.4f}"
    )

    print(
        f"IV       : "
        f"{selected_ce['implied_volatility'] * 100:.2f}%"
    )

else:
    print(
        "\nSELECTED CALL: NO VALID STRIKE"
    )


if selected_pe:

    print("\nSELECTED PUT:")

    print(
        f"Contract : {selected_pe['tradingsymbol']}"
    )

    print(
        f"Strike   : {selected_pe['strike']:.0f} PE"
    )

    print(
        f"LTP      : {selected_pe['ltp']:.2f}"
    )

    print(
        f"Delta    : {selected_pe['delta']:+.4f}"
    )

    print(
        f"IV       : "
        f"{selected_pe['implied_volatility'] * 100:.2f}%"
    )

else:
    print(
        "\nSELECTED PUT: NO VALID STRIKE"
    )

print("\n" + "=" * 80)


# ============================================================
# PE STRIKE DIAGNOSTIC
# ============================================================

print("\n" + "=" * 90)
print("🔬 PE STRIKE DIAGNOSTIC — 23400 TO 23650")
print("=" * 90)

diagnostic_strikes = {
    23400,
    23450,
    23500,
    23550,
    23600,
    23650,
}

liquid_symbols = {
    option["tradingsymbol"]
    for option in liquid_options
}

greeks_symbols = {
    option["tradingsymbol"]
    for option in greeks_options
}

for option in live_options:

    strike = int(option["strike"])

    if (
        option["option_type"] == "PE"
        and strike in diagnostic_strikes
    ):

        symbol = option["tradingsymbol"]

        if symbol not in liquid_symbols:
            status = "REJECTED BY LIQUIDITY"

        elif symbol not in greeks_symbols:
            status = "REJECTED BY IV/GREEKS"

        else:
            status = "PASSED"

        spread = option["spread_pct"]

        if spread is None:
            spread_text = "N/A"
        else:
            spread_text = f"{spread:.2f}%"

        print(
            f"{strike} PE | "
            f"LTP {option['ltp']:.2f} | "
            f"Spread {spread_text} | "
            f"Vol {option['volume']} | "
            f"OI {option['oi']} | "
            f"{status}"
        )

print("=" * 90)


# ============================================================
# EXECUTION QUALITY GATE TEST
# ============================================================

print("\n" + "=" * 80)
print("🛡️ EXECUTION QUALITY GATE")
print("=" * 80)

ce_quality = selector.check_execution_quality(
    option=selected_ce,
    max_spread_pct=4.0,
    min_volume=100000,
    min_oi=50000,
)

pe_quality = selector.check_execution_quality(
    option=selected_pe,
    max_spread_pct=4.0,
    min_volume=100000,
    min_oi=50000,
)

print("\nCALL LEG:")

if selected_ce:
    print(
        f"Contract : {selected_ce['tradingsymbol']}"
    )
    print(
        f"Strike   : {selected_ce['strike']:.0f} CE"
    )
    print(
        f"Delta    : {selected_ce['delta']:+.4f}"
    )

print(
    f"Status   : "
    f"{'APPROVED' if ce_quality['approved'] else 'REJECTED'}"
)

for reason in ce_quality["reasons"]:
    print(f"• {reason}")


print("\nPUT LEG:")

if selected_pe:
    print(
        f"Contract : {selected_pe['tradingsymbol']}"
    )
    print(
        f"Strike   : {selected_pe['strike']:.0f} PE"
    )
    print(
        f"Delta    : {selected_pe['delta']:+.4f}"
    )

print(
    f"Status   : "
    f"{'APPROVED' if pe_quality['approved'] else 'REJECTED'}"
)

for reason in pe_quality["reasons"]:
    print(f"• {reason}")

print("\n" + "=" * 80)

both_approved = (
    ce_quality["approved"]
    and pe_quality["approved"]
)

print(
    "SHORT STRANGLE EXECUTION STATUS : "
    + (
        "APPROVED"
        if both_approved
        else "BLOCKED"
    )
)

print("=" * 80)