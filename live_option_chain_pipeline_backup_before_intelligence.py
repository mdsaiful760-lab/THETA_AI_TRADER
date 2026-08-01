# ============================================================
# THETA AI TRADER
# LIVE OPTION CHAIN PIPELINE — FORWARD / BLACK-76 — READ ONLY
# ============================================================

import os
from datetime import date, datetime

from dotenv import load_dotenv
from kiteconnect import KiteConnect

from market_data_adapter import MarketDataAdapter
from index_option_forward_engine import IndexOptionForwardEngine
from option_greeks_engine import OptionGreeksEngine


# ============================================================
# SAFETY
# ============================================================

READ_ONLY_MODE = True

STRIKES_EACH_SIDE = 10

UNDERLYING = "NIFTY"
SPOT_SYMBOL = "NSE:NIFTY 50"
DERIVATIVE_EXCHANGE = "NFO"

PRICING_MODE = "FORWARD"


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
# CALCULATE TIME TO EXPIRY
# ============================================================

def calculate_time_to_expiry(
    greeks_engine,
    expiry,
    current_time,
):
    time_to_expiry = (
        greeks_engine.calculate_time_to_expiry(
            expiry=expiry,
            current_time=current_time,
        )
    )

    if time_to_expiry is None:
        raise RuntimeError(
            "Unable to calculate time to expiry."
        )

    time_to_expiry = float(
        time_to_expiry
    )

    if time_to_expiry <= 0:
        raise RuntimeError(
            "Time to expiry must be positive."
        )

    return time_to_expiry


# ============================================================
# ESTIMATE IMPLIED FORWARD
# ============================================================

def calculate_implied_forward(
    forward_engine,
    contracts,
    spot,
    time_to_expiry,
):
    result = (
        forward_engine.estimate_forward(
            contracts=contracts,
            spot_price=spot,
            time_to_expiry=time_to_expiry,
        )
    )

    if not result.get(
        "forward_allowed",
        False,
    ):
        raise RuntimeError(
            "IndexOptionForwardEngine blocked implied forward. "
            f"Reason: {result.get('reason')} | "
            f"Validation Errors: "
            f"{result.get('validation_errors', [])}"
        )

    implied_forward = result.get(
        "implied_forward"
    )

    if implied_forward is None:
        raise RuntimeError(
            "Forward engine allowed calculation but "
            "returned no implied forward."
        )

    implied_forward = float(
        implied_forward
    )

    if implied_forward <= 0:
        raise RuntimeError(
            "Forward engine returned invalid implied forward."
        )

    return result


# ============================================================
# CALCULATE LIVE FORWARD-MODE GREEKS
# ============================================================

def calculate_live_greeks(
    greeks_engine,
    contracts,
    spot,
    implied_forward,
    current_time,
):
    result = (
        greeks_engine.enrich_option_chain(
            contracts=contracts,
            spot_price=spot,
            current_time=current_time,
            pricing_mode=PRICING_MODE,
            implied_forward=implied_forward,
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
# DISPLAY FORWARD DIAGNOSTICS
# ============================================================

def display_forward_diagnostics(
    forward_result,
):
    section(
        "INDEX IMPLIED FORWARD DIAGNOSTICS"
    )

    print(
        "Forward Permission   :",
        forward_result.get(
            "forward_permission"
        ),
    )

    print(
        "Spot Price           :",
        safe_number(
            forward_result.get(
                "spot_price"
            ),
            2,
        ),
    )

    print(
        "Implied Forward      :",
        safe_number(
            forward_result.get(
                "implied_forward"
            ),
            2,
        ),
    )

    print(
        "Forward Basis        :",
        safe_number(
            forward_result.get(
                "basis"
            ),
            2,
        ),
    )

    print(
        "Forward Basis %      :",
        safe_number(
            forward_result.get(
                "basis_pct"
            ),
            4,
        ),
    )

    print(
        "Reference Strike     :",
        safe_number(
            forward_result.get(
                "reference_strike"
            ),
            0,
        ),
    )

    print(
        "Complete CE/PE Pairs :",
        forward_result.get(
            "pair_count"
        ),
    )

    print(
        "Valid Forward Pairs  :",
        forward_result.get(
            "valid_pair_count"
        ),
    )

    print(
        "Aggregation Pairs    :",
        forward_result.get(
            "aggregation_pair_count"
        ),
    )

    print(
        "Rejected Pairs       :",
        forward_result.get(
            "rejected_pair_count"
        ),
    )

    print(
        "Forward Min          :",
        safe_number(
            forward_result.get(
                "forward_min"
            ),
            2,
        ),
    )

    print(
        "Forward Max          :",
        safe_number(
            forward_result.get(
                "forward_max"
            ),
            2,
        ),
    )

    print(
        "Forward Range        :",
        safe_number(
            forward_result.get(
                "forward_range"
            ),
            2,
        ),
    )

    print(
        "Forward Range %      :",
        safe_number(
            forward_result.get(
                "forward_range_pct"
            ),
            4,
        ),
    )

    print(
        "Diagnostic Quality   :",
        forward_result.get(
            "quality"
        ),
    )

    print(
        "Pricing Mode         :",
        PRICING_MODE,
    )

    print(
        "Greeks Model         : BLACK-76"
    )


# ============================================================
# DISPLAY FORWARD PAIR REJECTIONS
# ============================================================

def display_forward_rejections(
    forward_result,
):
    rejections = (
        forward_result.get(
            "rejections",
            []
        )
    )

    if not rejections:
        return

    section(
        "FORWARD ESTIMATION REJECTIONS"
    )

    for rejection in rejections:
        print(
            " -",
            rejection
        )


# ============================================================
# DISPLAY CONTRACT REJECTIONS
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
        "LIVE NIFTY OPTION CHAIN + FORWARD-MODE GREEKS"
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
    forward_result,
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

    forward_broker_allowed = (
        forward_result.get(
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
        "Forward Broker Auth  :",
        forward_broker_allowed,
    )

    print(
        "Greeks Broker Auth   :",
        greeks_broker_allowed,
    )

    print(
        "Pricing Mode         :",
        greeks_result.get(
            "pricing_mode",
            PRICING_MODE,
        ),
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

    if forward_broker_allowed is not False:
        raise RuntimeError(
            "IndexOptionForwardEngine unexpectedly "
            "contains broker authority."
        )

    if greeks_broker_allowed is not False:
        raise RuntimeError(
            "OptionGreeksEngine unexpectedly "
            "contains broker authority."
        )

    if not forward_result.get(
        "forward_allowed",
        False,
    ):
        raise RuntimeError(
            "Safety audit received blocked forward result."
        )

    if (
        str(
            greeks_result.get(
                "pricing_mode",
                PRICING_MODE,
            )
        ).upper()
        != "FORWARD"
    ):
        raise RuntimeError(
            "Greeks engine did not remain in FORWARD mode."
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
        "🔒 READ-ONLY FORWARD PIPELINE SAFETY AUDIT PASSED"
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
        "Pricing Mode        : FORWARD"
    )

    print(
        "Greeks Model        : BLACK-76"
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

    forward_engine = (
        IndexOptionForwardEngine()
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

    # Use ONE timestamp for forward + Greeks calculations.
    calculation_time = datetime.now()

    time_to_expiry = (
        calculate_time_to_expiry(
            greeks_engine=greeks_engine,
            expiry=nearest_expiry,
            current_time=calculation_time,
        )
    )

    print(
        "✅ Time to Expiry   :",
        f"{time_to_expiry:.8f} years",
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

    normalized_contracts = (
        adapter_result[
            "contracts"
        ]
    )

    # --------------------------------------------------------
    # IMPLIED FORWARD
    # --------------------------------------------------------

    print(
        "\nEstimating index implied forward..."
    )

    forward_result = (
        calculate_implied_forward(
            forward_engine=forward_engine,
            contracts=normalized_contracts,
            spot=spot,
            time_to_expiry=time_to_expiry,
        )
    )

    implied_forward = float(
        forward_result[
            "implied_forward"
        ]
    )

    print(
        "✅ Implied Forward  :",
        f"{implied_forward:.2f}",
    )

    print(
        "✅ Forward Basis    :",
        safe_number(
            forward_result.get(
                "basis"
            ),
            2,
        ),
    )

    print(
        "✅ Forward Quality  :",
        forward_result.get(
            "quality"
        ),
    )

    print(
        "✅ Valid Pairs      :",
        forward_result.get(
            "valid_pair_count"
        ),
    )

    # --------------------------------------------------------
    # FORWARD-MODE GREEKS
    # --------------------------------------------------------

    print(
        "\nCalculating Black-76 IV + Greeks..."
    )

    greeks_result = (
        calculate_live_greeks(
            greeks_engine=greeks_engine,
            contracts=normalized_contracts,
            spot=spot,
            implied_forward=implied_forward,
            current_time=calculation_time,
        )
    )

    print(
        "✅ Pricing Mode     :",
        greeks_result.get(
            "pricing_mode",
            PRICING_MODE,
        ),
    )

    print(
        "✅ Greeks Enriched  :",
        greeks_result.get(
            "enriched_count"
        ),
    )

    print(
        "⚠️ Greeks Rejected  :",
        greeks_result.get(
            "rejected_count"
        ),
    )

    # --------------------------------------------------------
    # DIAGNOSTICS
    # --------------------------------------------------------

    display_forward_diagnostics(
        forward_result
    )

    # --------------------------------------------------------
    # DISPLAY OPTION CHAIN
    # --------------------------------------------------------

    display_chain(
        contracts=greeks_result[
            "contracts"
        ],
        atm=atm,
    )

    # --------------------------------------------------------
    # REJECTIONS
    # --------------------------------------------------------

    display_forward_rejections(
        forward_result
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
        forward_result=forward_result,
        greeks_result=greeks_result,
    )

    # --------------------------------------------------------
    # COMPLETE
    # --------------------------------------------------------

    section(
        "LIVE FORWARD PIPELINE COMPLETE"
    )

    print(
        "✅ Real Zerodha market data received"
    )

    print(
        "✅ Market data normalized"
    )

    print(
        "✅ Index implied forward estimated"
    )

    print(
        "✅ Black-76 IV calculated"
    )

    print(
        "✅ Forward Delta/Gamma/Theta/Vega calculated"
    )

    print(
        "🔒 No SPOT fallback used"
    )

    print(
        "🔒 No contract selection performed"
    )

    print(
        "🔒 No trade decision performed"
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