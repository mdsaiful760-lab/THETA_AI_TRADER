# ============================================================
# THETA AI TRADER
# OPTION CONTRACT SELECTOR — SAFETY TEST SUITE
# ============================================================

from option_contract_selector import OptionContractSelector


PASSED = 0
TOTAL = 0


# ============================================================
# DISPLAY HELPERS
# ============================================================

def line(char="=", length=78):
    print(char * length)


def heading(title):
    print()
    line()
    print(title)
    line()


def print_result(result):

    print(
        "Selection Permission :",
        result["selection_permission"],
    )

    print(
        "Selection Allowed    :",
        result["selection_allowed"],
    )

    print(
        "Reason               :",
        result["reason"],
    )

    print(
        "Profile              :",
        result["profile"],
    )

    print(
        "Candidate Count      :",
        result["candidate_count"],
    )

    print(
        "Eligible Count       :",
        result["eligible_count"],
    )

    print(
        "Selected Score       :",
        result["selected_score"],
    )

    print(
        "Broker Order Allowed :",
        result["broker_order_allowed"],
    )

    selected = result["selected_contract"]

    if selected:

        print(
            "Selected Symbol      :",
            selected["tradingsymbol"],
        )

        print(
            "Selected Strike      :",
            selected["strike"],
        )

        print(
            "Selected Option Type :",
            selected["option_type"],
        )

        print(
            "Selected Delta       :",
            selected["delta"],
        )

        print(
            "Selected Spread %    :",
            selected["spread_pct"],
        )

        print(
            "Selected OI          :",
            selected["open_interest"],
        )

        print(
            "Selected Volume      :",
            selected["volume"],
        )

    print(
        "Validation Errors    :",
        result["validation_errors"],
    )


# ============================================================
# ASSERTION HELPERS
# ============================================================

def assert_equal(actual, expected, message):

    if actual != expected:

        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_true(value, message):

    if value is not True:

        raise AssertionError(
            f"{message}\n"
            f"Expected: True\n"
            f"Actual  : {value}"
        )


def assert_false(value, message):

    if value is not False:

        raise AssertionError(
            f"{message}\n"
            f"Expected: False\n"
            f"Actual  : {value}"
        )


def assert_contains(
    collection,
    expected,
    message,
):

    if expected not in collection:

        raise AssertionError(
            f"{message}\n"
            f"Expected item: {expected}\n"
            f"Actual       : {collection}"
        )


# ============================================================
# CONTRACT FACTORY
# ============================================================

def make_contract(
    symbol,
    strike,
    option_type,
    delta,
    ltp=100.0,
    bid=99.5,
    ask=100.5,
    volume=100000,
    open_interest=200000,
    underlying="NIFTY",
    exchange="NFO",
    expiry="2026-08-25",
    lot_size=75,
    iv=15.0,
):

    return {
        "underlying":
            underlying,

        "exchange":
            exchange,

        "tradingsymbol":
            symbol,

        "expiry":
            expiry,

        "strike":
            strike,

        "option_type":
            option_type,

        "lot_size":
            lot_size,

        "ltp":
            ltp,

        "bid":
            bid,

        "ask":
            ask,

        "volume":
            volume,

        "open_interest":
            open_interest,

        "delta":
            delta,

        "iv":
            iv,

        "instrument_token":
            123456,

        "exchange_token":
            654321,

        "tick_size":
            0.05,
    }


# ============================================================
# STANDARD BALANCED CHAIN
# ============================================================

def balanced_chain():

    return [
        make_contract(
            "NIFTY26AUG24800PE",
            24800,
            "PE",
            -0.12,
        ),

        make_contract(
            "NIFTY26AUG24900PE",
            24900,
            "PE",
            -0.16,
        ),

        make_contract(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            -0.18,
        ),

        make_contract(
            "NIFTY26AUG25100PE",
            25100,
            "PE",
            -0.21,
        ),

        make_contract(
            "NIFTY26AUG25200PE",
            25200,
            "PE",
            -0.26,
        ),
    ]


# ============================================================
# TEST 1
# BALANCED PROFILE TARGET DELTA
# ============================================================

def test_balanced_target_delta():

    selector = OptionContractSelector(
        default_profile="BALANCED"
    )

    result = selector.select_contract(
        contracts=balanced_chain(),
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
        profile="BALANCED",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "ALLOW",
        "Balanced selection should succeed",
    )

    assert_equal(
        result[
            "selected_contract"
        ]["tradingsymbol"],
        "NIFTY26AUG25000PE",
        "Balanced profile should prefer "
        "delta closest to 0.18",
    )

    assert_equal(
        result[
            "selected_contract"
        ]["abs_delta"],
        0.18,
        "Selected absolute delta incorrect",
    )

    print(
        "✅ PASS — BALANCED profile selected "
        "target-delta contract"
    )


# ============================================================
# TEST 2
# CONSERVATIVE PROFILE
# ============================================================

def test_conservative_profile():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "NIFTY26AUG24700PE",
            24700,
            "PE",
            -0.10,
        ),

        make_contract(
            "NIFTY26AUG24800PE",
            24800,
            "PE",
            -0.13,
        ),

        make_contract(
            "NIFTY26AUG24900PE",
            24900,
            "PE",
            -0.16,
        ),

        make_contract(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            -0.18,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
        profile="CONSERVATIVE",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "ALLOW",
        "Conservative selection should succeed",
    )

    assert_equal(
        result[
            "selected_contract"
        ]["tradingsymbol"],
        "NIFTY26AUG24800PE",
        "Conservative profile should target "
        "approximately 0.13 delta",
    )

    print(
        "✅ PASS — CONSERVATIVE profile selected "
        "lower-delta contract"
    )


# ============================================================
# TEST 3
# CE / PE SEPARATION
# ============================================================

def test_option_type_separation():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "NIFTY26AUG25000CE",
            25000,
            "CE",
            0.18,
        ),

        make_contract(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            -0.18,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result[
            "selected_contract"
        ]["option_type"],
        "PE",
        "PE request must never select CE",
    )

    assert_equal(
        result[
            "selected_contract"
        ]["tradingsymbol"],
        "NIFTY26AUG25000PE",
        "Wrong option contract selected",
    )

    print(
        "✅ PASS — CE and PE contracts cannot mix"
    )


# ============================================================
# TEST 4
# WRONG UNDERLYING / EXPIRY / EXCHANGE
# ============================================================

def test_request_matching():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "BANKNIFTY_WRONG",
            55000,
            "PE",
            -0.18,
            underlying="BANKNIFTY",
        ),

        make_contract(
            "NIFTY_WRONG_EXPIRY",
            25000,
            "PE",
            -0.18,
            expiry="2026-09-01",
        ),

        make_contract(
            "NIFTY_WRONG_EXCHANGE",
            25000,
            "PE",
            -0.18,
            exchange="BFO",
        ),

        make_contract(
            "NIFTY_CORRECT",
            25000,
            "PE",
            -0.18,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result[
            "selected_contract"
        ]["tradingsymbol"],
        "NIFTY_CORRECT",
        "Selector must enforce underlying, "
        "expiry and exchange",
    )

    print(
        "✅ PASS — Underlying / expiry / exchange "
        "filters enforced"
    )


# ============================================================
# TEST 5
# LOW OPEN INTEREST
# ============================================================

def test_low_open_interest():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "LOW_OI",
            25000,
            "PE",
            -0.18,
            open_interest=100,
        )
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Low OI contract must not be selected",
    )

    assert_contains(
        result[
            "evaluations"
        ][0]["rejection_reasons"],
        "OPEN_INTEREST_TOO_LOW",
        "Low OI rejection missing",
    )

    print(
        "✅ PASS — Illiquid low-OI contract rejected"
    )


# ============================================================
# TEST 6
# LOW VOLUME
# ============================================================

def test_low_volume():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "LOW_VOLUME",
            25000,
            "PE",
            -0.18,
            volume=100,
        )
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Low-volume contract must block",
    )

    assert_contains(
        result[
            "evaluations"
        ][0]["rejection_reasons"],
        "VOLUME_TOO_LOW",
        "Low volume rejection missing",
    )

    print(
        "✅ PASS — Low-volume contract rejected"
    )


# ============================================================
# TEST 7
# INVALID BID / ASK
# ============================================================

def test_invalid_bid_ask():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "NO_BID",
            25000,
            "PE",
            -0.18,
            bid=0,
            ask=100,
        ),

        make_contract(
            "INVERTED_BOOK",
            25100,
            "PE",
            -0.18,
            bid=105,
            ask=100,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Invalid market quotes must block",
    )

    for evaluation in result["evaluations"]:

        assert_contains(
            evaluation["rejection_reasons"],
            "INVALID_BID_ASK",
            "Invalid bid/ask was not rejected",
        )

    print(
        "✅ PASS — Invalid bid/ask books rejected"
    )


# ============================================================
# TEST 8
# EXCESSIVE SPREAD
# ============================================================

def test_wide_spread():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "WIDE_SPREAD",
            25000,
            "PE",
            -0.18,
            bid=90,
            ask=110,
        )
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Wide spread must block selection",
    )

    assert_contains(
        result[
            "evaluations"
        ][0]["rejection_reasons"],
        "SPREAD_TOO_WIDE",
        "Wide spread rejection missing",
    )

    print(
        "✅ PASS — Excessive bid/ask spread rejected"
    )


# ============================================================
# TEST 9
# DELTA OUTSIDE RANGE
# ============================================================

def test_delta_range():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "DELTA_TOO_LOW",
            24500,
            "PE",
            -0.05,
        ),

        make_contract(
            "DELTA_TOO_HIGH",
            25200,
            "PE",
            -0.40,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
        profile="BALANCED",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Out-of-range delta contracts must block",
    )

    for evaluation in result["evaluations"]:

        assert_contains(
            evaluation["rejection_reasons"],
            "DELTA_OUTSIDE_RANGE",
            "Delta-range rejection missing",
        )

    print(
        "✅ PASS — Delta boundaries enforced"
    )


# ============================================================
# TEST 10
# PREMIUM FILTER
# ============================================================

def test_premium_filter():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "PREMIUM_TOO_LOW",
            25000,
            "PE",
            -0.18,
            ltp=10,
        ),

        make_contract(
            "PREMIUM_TOO_HIGH",
            25100,
            "PE",
            -0.18,
            ltp=250,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
        profile="BALANCED",
        custom_config={
            "min_premium": 20,
            "max_premium": 200,
        },
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Premium filters should reject both contracts",
    )

    assert_contains(
        result[
            "evaluations"
        ][0]["rejection_reasons"],
        "PREMIUM_TOO_LOW",
        "Minimum premium filter missing",
    )

    assert_contains(
        result[
            "evaluations"
        ][1]["rejection_reasons"],
        "PREMIUM_TOO_HIGH",
        "Maximum premium filter missing",
    )

    print(
        "✅ PASS — Premium range enforced"
    )


# ============================================================
# TEST 11
# CUSTOM DASHBOARD PROFILE
# ============================================================

def test_custom_profile():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "DELTA_10",
            24700,
            "PE",
            -0.10,
        ),

        make_contract(
            "DELTA_12",
            24800,
            "PE",
            -0.12,
        ),

        make_contract(
            "DELTA_15",
            24900,
            "PE",
            -0.15,
        ),
    ]

    custom = {
        "min_abs_delta": 0.08,
        "max_abs_delta": 0.14,
        "target_abs_delta": 0.12,

        "min_premium": 10,
        "max_premium": 500,

        "min_open_interest": 1000,
        "min_volume": 1000,

        "max_spread_pct": 3.0,
    }

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
        profile="CUSTOM",
        custom_config=custom,
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "ALLOW",
        "Valid custom profile should work",
    )

    assert_equal(
        result[
            "selected_contract"
        ]["tradingsymbol"],
        "DELTA_12",
        "Custom target delta not respected",
    )

    assert_equal(
        result["profile"],
        "CUSTOM",
        "Custom profile audit value incorrect",
    )

    print(
        "✅ PASS — Dashboard-style CUSTOM "
        "configuration supported"
    )


# ============================================================
# TEST 12
# INVALID CONTRACT METADATA
# ============================================================

def test_invalid_metadata():

    selector = OptionContractSelector()

    invalid = make_contract(
        "",
        0,
        "PE",
        -0.18,
        lot_size=0,
    )

    result = selector.select_contract(
        contracts=[invalid],
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    reasons = (
        result[
            "evaluations"
        ][0]["rejection_reasons"]
    )

    assert_contains(
        reasons,
        "MISSING_TRADINGSYMBOL",
        "Missing symbol validation absent",
    )

    assert_contains(
        reasons,
        "INVALID_STRIKE",
        "Invalid strike validation absent",
    )

    assert_contains(
        reasons,
        "INVALID_LOT_SIZE",
        "Invalid lot-size validation absent",
    )

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Malformed instrument must block",
    )

    print(
        "✅ PASS — Invalid instrument metadata rejected"
    )


# ============================================================
# TEST 13
# DETERMINISTIC RANKING
# ============================================================

def test_deterministic_ranking():

    selector = OptionContractSelector()

    # Same delta.
    # Same spread.
    # Higher OI should win.

    contracts = [
        make_contract(
            "LOWER_OI",
            24900,
            "PE",
            -0.18,
            open_interest=50000,
            volume=100000,
        ),

        make_contract(
            "HIGHER_OI",
            25000,
            "PE",
            -0.18,
            open_interest=500000,
            volume=100000,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result[
            "selected_contract"
        ]["tradingsymbol"],
        "HIGHER_OI",
        "Higher-quality liquidity candidate "
        "should win deterministic ranking",
    )

    print(
        "✅ PASS — Eligible contracts ranked "
        "deterministically"
    )


# ============================================================
# TEST 14
# NO ELIGIBLE CONTRACT
# ============================================================

def test_no_eligible_contract():

    selector = OptionContractSelector()

    contracts = [
        make_contract(
            "BAD_1",
            24000,
            "PE",
            -0.03,
        ),

        make_contract(
            "BAD_2",
            24100,
            "PE",
            -0.04,
        ),
    ]

    result = selector.select_contract(
        contracts=contracts,
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "No eligible contract must return BLOCK",
    )

    assert_false(
        result["selection_allowed"],
        "Selection must not be allowed",
    )

    assert_equal(
        result["selected_contract"],
        None,
        "Blocked selection must have no contract",
    )

    assert_equal(
        result["eligible_count"],
        0,
        "Eligible count should be zero",
    )

    print(
        "✅ PASS — No suitable contract means NO TRADE"
    )


# ============================================================
# TEST 15
# TRADE PLAN COMPATIBILITY
# ============================================================

def test_trade_plan_compatibility():

    selector = OptionContractSelector()

    result = selector.select_contract(
        contracts=balanced_chain(),
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    selected = result[
        "selected_contract"
    ]

    required_fields = [
        "underlying",
        "exchange",
        "tradingsymbol",
        "expiry",
        "strike",
        "option_type",
        "lot_size",
    ]

    for field in required_fields:

        if field not in selected:

            raise AssertionError(
                "TradePlanEngine-required field "
                f"missing: {field}"
            )

        if selected[field] is None:

            raise AssertionError(
                "TradePlanEngine-required field "
                f"is None: {field}"
            )

    print(
        "Underlying    :",
        selected["underlying"],
    )

    print(
        "Exchange      :",
        selected["exchange"],
    )

    print(
        "Tradingsymbol :",
        selected["tradingsymbol"],
    )

    print(
        "Expiry        :",
        selected["expiry"],
    )

    print(
        "Strike        :",
        selected["strike"],
    )

    print(
        "Option Type   :",
        selected["option_type"],
    )

    print(
        "Lot Size      :",
        selected["lot_size"],
    )

    print()
    print(
        "✅ PASS — Selected contract contains "
        "TradePlanEngine-required fields"
    )


# ============================================================
# TEST 16
# BROKER AUTHORITY MUST ALWAYS BE FALSE
# ============================================================

def test_broker_authority():

    selector = OptionContractSelector()

    allowed = selector.select_contract(
        contracts=balanced_chain(),
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    blocked = selector.select_contract(
        contracts=[],
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    assert_false(
        allowed["broker_order_allowed"],
        "Successful selector unexpectedly "
        "received broker authority",
    )

    assert_false(
        blocked["broker_order_allowed"],
        "Blocked selector unexpectedly "
        "received broker authority",
    )

    print(
        "Allowed Result Broker Authority :",
        allowed["broker_order_allowed"],
    )

    print(
        "Blocked Result Broker Authority :",
        blocked["broker_order_allowed"],
    )

    print()
    print(
        "✅ PASS — OptionContractSelector has "
        "zero broker execution authority"
    )


# ============================================================
# TEST 17
# SELECTOR MUST NOT CONTROL RISK OR POSITION SIZE
# ============================================================

def test_no_risk_position_authority():

    selector = OptionContractSelector()

    result = selector.select_contract(
        contracts=balanced_chain(),
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
    )

    forbidden_result_fields = [
        "final_lots",
        "final_quantity",
        "authorized_risk",
        "final_authorized_risk_rupees",
        "daily_risk_budget_rupees",
        "remaining_daily_risk_rupees",
    ]

    forbidden_contract_fields = [
        "final_lots",
        "final_quantity",
        "authorized_risk",
        "final_authorized_risk_rupees",
    ]

    for field in forbidden_result_fields:

        if field in result:

            raise AssertionError(
                "Selector illegally contains "
                f"risk/position authority: {field}"
            )

    selected = result[
        "selected_contract"
    ]

    for field in forbidden_contract_fields:

        if field in selected:

            raise AssertionError(
                "Selected contract illegally contains "
                f"risk/position authority: {field}"
            )

    assert_false(
        result["broker_order_allowed"],
        "Selector must never have broker authority",
    )

    print(
        "Risk Allocation Authority : NONE"
    )

    print(
        "Position Size Authority   : NONE"
    )

    print(
        "Broker Authority          : NONE"
    )

    print()
    print(
        "✅ PASS — Selector decides WHAT contract, "
        "never HOW MUCH risk"
    )


# ============================================================
# TEST 18
# NORMALIZATION
# ============================================================

def test_normalization():

    selector = OptionContractSelector()

    contract = make_contract(
        " nifty26aug25000pe ",
        25000,
        " pe ",
        -0.18,
        underlying=" nifty ",
        exchange=" nfo ",
    )

    result = selector.select_contract(
        contracts=[contract],
        underlying=" nifty ",
        option_type=" pe ",
        side=" sell ",
        expiry="2026-08-25",
        exchange=" nfo ",
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "ALLOW",
        "Normalized contract should be allowed",
    )

    selected = result[
        "selected_contract"
    ]

    assert_equal(
        selected["underlying"],
        "NIFTY",
        "Underlying normalization failed",
    )

    assert_equal(
        selected["exchange"],
        "NFO",
        "Exchange normalization failed",
    )

    assert_equal(
        selected["option_type"],
        "PE",
        "Option type normalization failed",
    )

    assert_equal(
        selected["side"],
        "SELL",
        "Side normalization failed",
    )

    print(
        "✅ PASS — Contract request normalized safely"
    )


# ============================================================
# TEST 19
# INVALID CUSTOM CONFIGURATION
# ============================================================

def test_invalid_custom_configuration():

    selector = OptionContractSelector()

    invalid_config = {
        "min_abs_delta": 0.20,
        "max_abs_delta": 0.10,
        "target_abs_delta": 0.15,

        "min_premium": 20,

        "min_open_interest": 1000,
        "min_volume": 1000,

        "max_spread_pct": 2.0,
    }

    result = selector.select_contract(
        contracts=balanced_chain(),
        underlying="NIFTY",
        option_type="PE",
        side="SELL",
        expiry="2026-08-25",
        exchange="NFO",
        profile="CUSTOM",
        custom_config=invalid_config,
    )

    print_result(result)

    assert_equal(
        result["selection_permission"],
        "BLOCK",
        "Invalid profile configuration must block",
    )

    assert_equal(
        result["reason"],
        "INVALID_PROFILE_CONFIGURATION",
        "Wrong block reason",
    )

    assert_false(
        result["broker_order_allowed"],
        "Invalid profile must never receive "
        "broker authority",
    )

    print(
        "✅ PASS — Unsafe custom configuration rejected"
    )


# ============================================================
# TEST 20
# UNIVERSAL BLOCK INVARIANT
# ============================================================

def test_block_invariant():

    selector = OptionContractSelector()

    scenarios = [
        {
            "name":
                "EMPTY_CHAIN",

            "contracts":
                [],
        },

        {
            "name":
                "LOW_DELTA",

            "contracts": [
                make_contract(
                    "LOW_DELTA",
                    24000,
                    "PE",
                    -0.03,
                )
            ],
        },

        {
            "name":
                "NO_LIQUIDITY",

            "contracts": [
                make_contract(
                    "NO_LIQUIDITY",
                    25000,
                    "PE",
                    -0.18,
                    volume=0,
                    open_interest=0,
                )
            ],
        },

        {
            "name":
                "INVALID_QUOTES",

            "contracts": [
                make_contract(
                    "INVALID_QUOTES",
                    25000,
                    "PE",
                    -0.18,
                    bid=0,
                    ask=0,
                )
            ],
        },
    ]

    for scenario in scenarios:

        result = selector.select_contract(
            contracts=scenario[
                "contracts"
            ],
            underlying="NIFTY",
            option_type="PE",
            side="SELL",
            expiry="2026-08-25",
            exchange="NFO",
        )

        assert_equal(
            result["selection_permission"],
            "BLOCK",
            f"{scenario['name']} should block",
        )

        assert_false(
            result["selection_allowed"],
            f"{scenario['name']} unexpectedly allowed",
        )

        assert_equal(
            result["selected_contract"],
            None,
            f"{scenario['name']} retained a contract",
        )

        assert_false(
            result["broker_order_allowed"],
            f"{scenario['name']} received broker authority",
        )

        print(
            "Verified:",
            scenario["name"],
            "→ BLOCK / NO CONTRACT / NO BROKER AUTHORITY",
        )

    print()
    print(
        "✅ PASS — Every blocked selection guarantees "
        "no selected contract"
    )


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():

    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — BALANCED TARGET DELTA",
            test_balanced_target_delta,
        ),
        (
            "TEST 2 — CONSERVATIVE PROFILE",
            test_conservative_profile,
        ),
        (
            "TEST 3 — CE / PE SEPARATION",
            test_option_type_separation,
        ),
        (
            "TEST 4 — REQUEST MATCHING",
            test_request_matching,
        ),
        (
            "TEST 5 — OPEN INTEREST FILTER",
            test_low_open_interest,
        ),
        (
            "TEST 6 — VOLUME FILTER",
            test_low_volume,
        ),
        (
            "TEST 7 — BID / ASK SAFETY",
            test_invalid_bid_ask,
        ),
        (
            "TEST 8 — SPREAD SAFETY",
            test_wide_spread,
        ),
        (
            "TEST 9 — DELTA RANGE",
            test_delta_range,
        ),
        (
            "TEST 10 — PREMIUM FILTER",
            test_premium_filter,
        ),
        (
            "TEST 11 — CUSTOM PROFILE",
            test_custom_profile,
        ),
        (
            "TEST 12 — INSTRUMENT METADATA",
            test_invalid_metadata,
        ),
        (
            "TEST 13 — DETERMINISTIC RANKING",
            test_deterministic_ranking,
        ),
        (
            "TEST 14 — NO ELIGIBLE CONTRACT",
            test_no_eligible_contract,
        ),
        (
            "TEST 15 — TRADE PLAN COMPATIBILITY",
            test_trade_plan_compatibility,
        ),
        (
            "TEST 16 — BROKER AUTHORITY",
            test_broker_authority,
        ),
        (
            "TEST 17 — RISK/POSITION SEPARATION",
            test_no_risk_position_authority,
        ),
        (
            "TEST 18 — NORMALIZATION",
            test_normalization,
        ),
        (
            "TEST 19 — CONFIG SAFETY",
            test_invalid_custom_configuration,
        ),
        (
            "TEST 20 — UNIVERSAL BLOCK INVARIANT",
            test_block_invariant,
        ),
    ]

    TOTAL = len(tests)

    heading(
        "THETA AI TRADER — "
        "OPTION CONTRACT SELECTOR TEST SUITE"
    )

    print()
    print(
        "Live Market Data : NONE"
    )

    print(
        "Broker Connection: NONE"
    )

    print(
        "Order Placement  : DISABLED"
    )

    print(
        "Test Type        : CONTRACT SELECTION / SAFETY"
    )

    for title, test in tests:

        heading(
            title
        )

        try:

            test()

            PASSED += 1

        except Exception as error:

            print()
            print(
                "❌ TEST FAILED"
            )

            print(
                "Test :",
                test.__name__,
            )

            print(
                "Error:",
                error,
            )

            print()
            line()

            print(
                f"❌ OPTION CONTRACT SELECTOR "
                f"TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()

            raise

    print()
    line()

    print(
        f"✅ ALL OPTION CONTRACT SELECTOR "
        f"TESTS PASSED ({PASSED}/{TOTAL})"
    )

    print(
        "🔒 SELECTOR CONTROLS CONTRACT ONLY"
    )

    print(
        "🔒 SELECTOR HAS NO RISK/POSITION AUTHORITY"
    )

    print(
        "🔒 BLOCKED SELECTION = NO CONTRACT"
    )

    print(
        "🔒 BROKER ORDER PLACEMENT DISABLED"
    )

    line()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_all_tests()