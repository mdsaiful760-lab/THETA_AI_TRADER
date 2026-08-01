# ============================================================
# THETA AI TRADER
# LIVE OPTION CHAIN PIPELINE — READ ONLY
# ============================================================

import os
from datetime import date, datetime

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from market_data_adapter import MarketDataAdapter
from option_greeks_engine import OptionGreeksEngine


# ============================================================
# SAFETY
# ============================================================

READ_ONLY_MODE = True

# Number of strikes on EACH side of ATM.
STRIKES_EACH_SIDE = 10

UNDERLYING = "NIFTY"
SPOT_SYMBOL = "NSE:NIFTY 50"
DERIVATIVE_EXCHANGE = "NFO"


# ============================================================
# DISPLAY HELPERS
# ============================================================

def line(char="=", length=140):
    print(char * length)


def section(title):
    print()
    line()
    print(title)
    line()


def safe_number(value, decimals=2):
    if value is None:
        return "-"

    try:
        return f"{float(value):.{decimals}f}"
    except (TypeError, ValueError):
        return "-"


def safe_int(value):
    if value is None:
        return "-"

    try:
        return str(int(value))
    except (TypeError, ValueError):
        return "-"


# ============================================================
# KITE CONNECTION
# ============================================================

def create_kite_client():
    load_dotenv()

    api_key = os.getenv("KITE_API_KEY")
    access_token = os.getenv("KITE_ACCESS_TOKEN")

    if not api_key:
        raise RuntimeError(
            "KITE_API_KEY missing from .env"
        )

    if not access_token:
        raise RuntimeError(
            "KITE_ACCESS_TOKEN missing from .env. "
            "Run kite_login.py first."
        )

    kite = KiteConnect(
        api_key=api_key
    )

    kite.set_access_token(
        access_token
    )

    return kite


# ============================================================
# FETCH NIFTY SPOT
# ============================================================

def fetch_spot_price(kite):
    result = kite.ltp(
        SPOT_SYMBOL
    )

    if SPOT_SYMBOL not in result:
        raise RuntimeError(
            "NIFTY spot response missing from Kite."
        )

    spot = result[
        SPOT_SYMBOL
    ].get(
        "last_price"
    )

    if spot is None:
        raise RuntimeError(
            "NIFTY spot price missing."
        )

    spot = float(
        spot
    )

    if spot <= 0:
        raise RuntimeError(
            f"Invalid NIFTY spot price: {spot}"
        )

    return spot


# ============================================================
# DOWNLOAD NFO INSTRUMENT MASTER
# ============================================================

def fetch_nfo_instruments(kite):
    instruments = kite.instruments(
        DERIVATIVE_EXCHANGE
    )

    if not instruments:
        raise RuntimeError(
            "Kite returned empty NFO instrument master."
        )

    return instruments


# ============================================================
# FILTER NIFTY OPTIONS
# ============================================================

def filter_nifty_options(instruments):
    result = []

    for instrument in instruments:

        name = str(
            instrument.get(
                "name",
                ""
            )
        ).strip().upper()

        instrument_type = str(
            instrument.get(
                "instrument_type",
                ""
            )
        ).strip().upper()

        exchange = str(
            instrument.get(
                "exchange",
                ""
            )
        ).strip().upper()

        if name != UNDERLYING:
            continue

        if exchange != DERIVATIVE_EXCHANGE:
            continue

        if instrument_type not in (
            "CE",
            "PE",
        ):
            continue

        result.append(
            instrument
        )

    if not result:
        raise RuntimeError(
            "No NIFTY option contracts found in NFO."
        )

    return result


# ============================================================
# FIND NEAREST LIVE EXPIRY
# ============================================================

def find_nearest_expiry(
    adapter,
    instruments,
):
    nearest_expiry = (
        adapter.get_nearest_expiry(
            instruments=instruments,
            underlying=UNDERLYING,
            exchange=DERIVATIVE_EXCHANGE,
            reference_date=date.today(),
        )
    )

    if nearest_expiry is None:
        raise RuntimeError(
            "No future NIFTY expiry found."
        )

    return nearest_expiry


# ============================================================
# GET STRIKE WINDOW
# ============================================================

def build_strike_window(
    adapter,
    instruments,
    expiry,
    spot,
):
    strikes = (
        adapter.get_available_strikes(
            instruments=instruments,
            underlying=UNDERLYING,
            expiry=expiry,
            exchange=DERIVATIVE_EXCHANGE,
        )
    )

    if not strikes:
        raise RuntimeError(
            "No strikes found for selected NIFTY expiry."
        )

    atm = adapter.get_atm_strike(
        instruments=instruments,
        underlying=UNDERLYING,
        expiry=expiry,
        spot_price=spot,
        exchange=DERIVATIVE_EXCHANGE,
    )

    if atm is None:
        raise RuntimeError(
            "Unable to determine ATM strike."
        )

    strike_step = (
        adapter.detect_strike_step(
            strikes
        )
    )

    if strike_step is None:
        raise RuntimeError(
            "Unable to detect strike interval."
        )

    nearby_strikes = (
        adapter.get_nearby_strikes(
            strikes=strikes,
            spot_price=spot,
            strikes_each_side=STRIKES_EACH_SIDE,
        )
    )

    if not nearby_strikes:
        raise RuntimeError(
            "Unable to create nearby-strike window."
        )

    return {
        "strikes": strikes,
        "atm": float(atm),
        "strike_step": float(
            strike_step
        ),
        "nearby_strikes": [
            float(x)
            for x in nearby_strikes
        ],
    }


# ============================================================
# SELECT INSTRUMENTS FOR WINDOW
# ============================================================

def select_option_instruments(
    instruments,
    expiry,
    nearby_strikes,
):
    selected = []

    strike_set = set(
        float(x)
        for x in nearby_strikes
    )

    expiry_text = (
        expiry.isoformat()
        if hasattr(
            expiry,
            "isoformat"
        )
        else str(expiry)
    )

    for instrument in instruments:

        instrument_expiry = (
            instrument.get(
                "expiry"
            )
        )

        instrument_expiry_text = (
            instrument_expiry.isoformat()
            if hasattr(
                instrument_expiry,
                "isoformat"
            )
            else str(
                instrument_expiry
            )
        )

        if (
            instrument_expiry_text
            != expiry_text
        ):
            continue

        try:
            strike = float(
                instrument.get(
                    "strike",
                    0
                )
            )
        except (
            TypeError,
            ValueError,
        ):
            continue

        if strike not in strike_set:
            continue

        option_type = str(
            instrument.get(
                "instrument_type",
                ""
            )
        ).strip().upper()

        if option_type not in (
            "CE",
            "PE",
        ):
            continue

        selected.append(
            instrument
        )

    if not selected:
        raise RuntimeError(
            "No option instruments selected "
            "inside strike window."
        )

    return selected


# ============================================================
# FETCH OPTION QUOTES
# ============================================================

def fetch_option_quotes(
    kite,
    instruments,
):
    quote_keys = []

    for instrument in instruments:

        symbol = str(
            instrument.get(
                "tradingsymbol",
                ""
            )
        ).strip()

        if not symbol:
            continue

        quote_keys.append(
            f"{DERIVATIVE_EXCHANGE}:{symbol}"
        )

    if not quote_keys:
        raise RuntimeError(
            "No valid quote keys generated."
        )

    # Kite supports multiple instruments in quote().
    quotes = kite.quote(
        quote_keys
    )

    if not quotes:
        raise RuntimeError(
            "Kite returned no option quotes."
        )

    return quotes


# ============================================================
# NORMALIZE THROUGH MARKET DATA ADAPTER
# ============================================================

def normalize_option_chain(
    adapter,
    instruments,
    quotes,
    expiry,
):
    result = (
        adapter.build_option_chain(
            instruments=instruments,
            quotes=quotes,
            underlying=UNDERLYING,
            expiry=expiry,
            exchange=DERIVATIVE_EXCHANGE,
        )
    )

    if not result.get(
        "adapter_allowed",
        False,
    ):
        raise RuntimeError(
            "MarketDataAdapter blocked option chain. "
            f"Reason: {result.get('reason')}"
        )

    contracts = result.get(
        "contracts",
        []
    )

    if not contracts:
        raise RuntimeError(
            "MarketDataAdapter produced no valid contracts."
        )

    return result


# ============================================================
# CALCULATE LIVE GREEKS
# ============================================================

def calculate_live_greeks(
    greeks_engine,
    contracts,
    spot,
):
    result = (
        greeks_engine.enrich_option_chain(
            contracts=contracts,
            spot_price=spot,
            current_time=datetime.now(),
        )
    )

    if not result.get(
        "greeks_allowed",
        False,
    ):
        raise RuntimeError(
            "OptionGreeksEngine blocked complete chain. "
            f"Reason: {result.get('reason')}"
        )

    return result


# ============================================================
# DISPLAY REJECTIONS
# ============================================================

def display_rejections(
    adapter_result,
    greeks_result,
):
    adapter_rejections = (
        adapter_result.get(
            "rejections",
            []
        )
    )

    greeks_rejections = (
        greeks_result.get(
            "rejections",
            []
        )
    )

    if not (
        adapter_rejections
        or greeks_rejections
    ):
        return

    section(
        "REJECTED / UNUSABLE CONTRACTS"
    )

    if adapter_rejections:

        print(
            "\nMarketDataAdapter Rejections:"
        )

        for rejection in adapter_rejections:
            print(
                " -",
                rejection
            )

    if greeks_rejections:

        print(
            "\nOptionGreeksEngine Rejections:"
        )

        for rejection in greeks_rejections:
            print(
                " -",
                rejection
            )


# ============================================================
# DISPLAY LIVE OPTION TABLE
# ============================================================

def display_chain(
    contracts,
    atm,
):
    section(
        "LIVE NIFTY OPTION CHAIN + CALCULATED GREEKS"
    )

    header = (
        f"{'STRIKE':>8} "
        f"{'TYPE':>5} "
        f"{'LTP':>9} "
        f"{'BID':>9} "
        f"{'ASK':>9} "
        f"{'OI':>12} "
        f"{'IV%':>8} "
        f"{'DELTA':>9} "
        f"{'GAMMA':>11} "
        f"{'THETA':>10} "
        f"{'VEGA':>9} "
        f"{'SRC':>9}"
    )

    print(
        header
    )

    print(
        "-" * len(
            header
        )
    )

    for contract in contracts:

        strike = float(
            contract.get(
                "strike",
                0
            )
        )

        option_type = (
            contract.get(
                "option_type",
                "-"
            )
        )

        atm_marker = (
            "*"
            if abs(
                strike - atm
            ) < 0.000001
            else " "
        )

        print(
            f"{atm_marker}"
            f"{strike:>7.0f} "
            f"{option_type:>5} "
            f"{safe_number(contract.get('ltp')):>9} "
            f"{safe_number(contract.get('bid')):>9} "
            f"{safe_number(contract.get('ask')):>9} "
            f"{safe_int(contract.get('open_interest')):>12} "
            f"{safe_number(contract.get('iv')):>8} "
            f"{safe_number(contract.get('delta'), 4):>9} "
            f"{safe_number(contract.get('gamma'), 6):>11} "
            f"{safe_number(contract.get('theta'), 4):>10} "
            f"{safe_number(contract.get('vega'), 4):>9} "
            f"{str(contract.get('greeks_price_source', '-')):>9}"
        )


# ============================================================
# SAFETY AUDIT
# ============================================================

def safety_audit(
    adapter_result,
    greeks_result,
):
    section(
        "PIPELINE SAFETY AUDIT"
    )

    adapter_broker_allowed = (
        adapter_result.get(
            "broker_order_allowed"
        )
    )

    greeks_broker_allowed = (
        greeks_result.get(
            "broker_order_allowed"
        )
    )

    print(
        "Read Only Mode       :",
        READ_ONLY_MODE,
    )

    print(
        "Adapter Broker Auth  :",
        adapter_broker_allowed,
    )

    print(
        "Greeks Broker Auth   :",
        greeks_broker_allowed,
    )

    if READ_ONLY_MODE is not True:
        raise RuntimeError(
            "READ_ONLY_MODE safety invariant broken."
        )

    if adapter_broker_allowed is not False:
        raise RuntimeError(
            "MarketDataAdapter unexpectedly "
            "contains broker authority."
        )

    if greeks_broker_allowed is not False:
        raise RuntimeError(
            "OptionGreeksEngine unexpectedly "
            "contains broker authority."
        )

    print()
    print(
        "Contract Selection   : DISABLED"
    )
    print(
        "Trade Decision       : DISABLED"
    )
    print(
        "Risk Allocation      : DISABLED"
    )
    print(
        "Position Sizing      : DISABLED"
    )
    print(
        "Order Placement      : DISABLED"
    )

    print()
    print(
        "🔒 READ-ONLY SAFETY AUDIT PASSED"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    section(
        "THETA AI TRADER — LIVE OPTION CHAIN PIPELINE"
    )

    print(
        "Mode                : READ ONLY"
    )

    print(
        "Underlying          : NIFTY 50"
    )

    print(
        "Broker              : ZERODHA KITE"
    )

    print(
        "Order Placement     : DISABLED"
    )

    # --------------------------------------------------------
    # CREATE ENGINES
    # --------------------------------------------------------

    adapter = (
        MarketDataAdapter()
    )

    greeks_engine = (
        OptionGreeksEngine()
    )

    # --------------------------------------------------------
    # CONNECT KITE
    # --------------------------------------------------------

    print(
        "\nConnecting to Kite..."
    )

    kite = (
        create_kite_client()
    )

    print(
        "✅ Kite client initialized"
    )

    # --------------------------------------------------------
    # SPOT
    # --------------------------------------------------------

    print(
        "\nFetching NIFTY spot..."
    )

    spot = fetch_spot_price(
        kite
    )

    print(
        f"✅ NIFTY Spot : {spot:.2f}"
    )

    # --------------------------------------------------------
    # INSTRUMENT MASTER
    # --------------------------------------------------------

    print(
        "\nDownloading NFO instrument master..."
    )

    instruments = (
        fetch_nfo_instruments(
            kite
        )
    )

    print(
        "✅ NFO Instruments :",
        len(
            instruments
        )
    )

    # --------------------------------------------------------
    # NIFTY OPTIONS
    # --------------------------------------------------------

    nifty_options = (
        filter_nifty_options(
            instruments
        )
    )

    print(
        "✅ NIFTY Options    :",
        len(
            nifty_options
        )
    )

    # --------------------------------------------------------
    # EXPIRY
    # --------------------------------------------------------

    nearest_expiry = (
        find_nearest_expiry(
            adapter,
            nifty_options,
        )
    )

    print(
        "✅ Nearest Expiry   :",
        nearest_expiry,
    )

    # --------------------------------------------------------
    # STRIKE WINDOW
    # --------------------------------------------------------

    strike_info = (
        build_strike_window(
            adapter=adapter,
            instruments=nifty_options,
            expiry=nearest_expiry,
            spot=spot,
        )
    )

    atm = strike_info[
        "atm"
    ]

    strike_step = (
        strike_info[
            "strike_step"
        ]
    )

    nearby_strikes = (
        strike_info[
            "nearby_strikes"
        ]
    )

    print(
        f"✅ ATM Strike       : {atm:.0f}"
    )

    print(
        f"✅ Strike Step      : {strike_step:.0f}"
    )

    print(
        "✅ Strike Window    :",
        f"{nearby_strikes[0]:.0f}",
        "→",
        f"{nearby_strikes[-1]:.0f}",
    )

    # --------------------------------------------------------
    # SELECT CONTRACTS
    # --------------------------------------------------------

    selected_instruments = (
        select_option_instruments(
            instruments=nifty_options,
            expiry=nearest_expiry,
            nearby_strikes=nearby_strikes,
        )
    )

    print(
        "✅ Selected Contracts:",
        len(
            selected_instruments
        )
    )

    # --------------------------------------------------------
    # LIVE QUOTES
    # --------------------------------------------------------

    print(
        "\nFetching live option quotes..."
    )

    quotes = (
        fetch_option_quotes(
            kite=kite,
            instruments=selected_instruments,
        )
    )

    print(
        "✅ Quotes Received  :",
        len(
            quotes
        )
    )

    # --------------------------------------------------------
    # ADAPTER
    # --------------------------------------------------------

    print(
        "\nNormalizing market data..."
    )

    adapter_result = (
        normalize_option_chain(
            adapter=adapter,
            instruments=selected_instruments,
            quotes=quotes,
            expiry=nearest_expiry,
        )
    )

    print(
        "✅ Normalized       :",
        adapter_result.get(
            "normalized_count"
        )
    )

    print(
        "⚠️ Adapter Rejected :",
        adapter_result.get(
            "rejected_count"
        )
    )

    # --------------------------------------------------------
    # GREEKS
    # --------------------------------------------------------

    print(
        "\nCalculating IV + Greeks..."
    )

    greeks_result = (
        calculate_live_greeks(
            greeks_engine=greeks_engine,
            contracts=adapter_result[
                "contracts"
            ],
            spot=spot,
        )
    )

    print(
        "✅ Greeks Enriched  :",
        greeks_result.get(
            "enriched_count"
        )
    )

    print(
        "⚠️ Greeks Rejected  :",
        greeks_result.get(
            "rejected_count"
        )
    )

    # --------------------------------------------------------
    # DISPLAY
    # --------------------------------------------------------

    display_chain(
        contracts=greeks_result[
            "contracts"
        ],
        atm=atm,
    )

    display_rejections(
        adapter_result=adapter_result,
        greeks_result=greeks_result,
    )

    # --------------------------------------------------------
    # SAFETY
    # --------------------------------------------------------

    safety_audit(
        adapter_result=adapter_result,
        greeks_result=greeks_result,
    )

    section(
        "LIVE PIPELINE COMPLETE"
    )

    print(
        "✅ Real Zerodha market data received"
    )

    print(
        "✅ Market data normalized"
    )

    print(
        "✅ IV calculated"
    )

    print(
        "✅ Delta/Gamma/Theta/Vega calculated"
    )

    print(
        "🔒 No contract selection performed"
    )

    print(
        "🔒 No risk authorization performed"
    )

    print(
        "🔒 No position sizing performed"
    )

    print(
        "🔒 No broker orders placed"
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()