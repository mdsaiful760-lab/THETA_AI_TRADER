# ============================================================
# THETA AI TRADER
# TRADE PLAN ENGINE — SAFETY TEST SUITE
# ============================================================

from trade_plan_engine import TradePlanEngine


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
        "Plan Permission       :",
        result["plan_permission"],
    )

    print(
        "Plan Allowed          :",
        result["plan_allowed"],
    )

    print(
        "Reason                :",
        result["reason"],
    )

    print(
        "Trade ID              :",
        result["trade_id"],
    )

    print(
        "Intent Created        :",
        result["order_intent_created"],
    )

    print(
        "Broker Order Allowed  :",
        result["broker_order_allowed"],
    )

    print(
        "Authorized Lots       :",
        result["authorized_lots"],
    )

    print(
        "Authorized Quantity   :",
        result["authorized_quantity"],
    )

    print(
        "Authorized Risk       :",
        result["final_authorized_risk_rupees"],
    )

    print(
        "Validation Errors     :",
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


def assert_close(
    actual,
    expected,
    message,
    tolerance=0.02,
):

    if abs(
        float(actual)
        - float(expected)
    ) > tolerance:

        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
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
# STANDARD APPROVED ORCHESTRATOR RESULT
# ============================================================

def approved_orchestrator_result(
    lots=8,
    lot_size=75,
    authorized_risk=10000.0,
    estimated_max_loss=9000.0,
    setup_score=92.0,
):

    quantity = lots * lot_size

    return {
        "final_permission":
            "ALLOW",

        "trade_allowed":
            True,

        "final_block_reason":
            None,

        "order_placement_enabled":
            False,

        "capital":
            1000000.0,

        "daily_risk_budget_rupees":
            30000.0,

        "remaining_daily_risk_rupees":
            30000.0,

        "risk_engine_limit_rupees":
            authorized_risk,

        "allocator_limit_rupees":
            authorized_risk,

        "final_authorized_risk_rupees":
            authorized_risk,

        "trades_taken_today":
            0,

        "setup_score":
            setup_score,

        "is_expiry_day":
            False,

        "final_lots":
            lots,

        "final_quantity":
            quantity,

        "estimated_max_loss":
            estimated_max_loss,

        "estimated_margin_required":
            lots * 10000.0,

        "limiting_factor":
            "RISK_BUDGET",

        "risk_management":
            {},

        "risk_budget":
            {},

        "position_sizing":
            {},
    }


# ============================================================
# STANDARD BLOCKED ORCHESTRATOR RESULT
# ============================================================

def blocked_orchestrator_result():

    return {
        "final_permission":
            "BLOCK",

        "trade_allowed":
            False,

        "final_block_reason":
            "RISK_MANAGEMENT_BLOCK",

        "order_placement_enabled":
            False,

        "final_authorized_risk_rupees":
            0.0,

        "setup_score":
            95.0,

        "final_lots":
            0,

        "final_quantity":
            0,

        "estimated_max_loss":
            0.0,
    }


# ============================================================
# STANDARD PLAN CALL
# ============================================================

def create_standard_plan(
    engine,
    orchestrator_result=None,
    **overrides,
):

    if orchestrator_result is None:

        orchestrator_result = (
            approved_orchestrator_result()
        )

    kwargs = {
        "orchestrator_result":
            orchestrator_result,

        "underlying":
            "NIFTY",

        "exchange":
            "NFO",

        "tradingsymbol":
            "NIFTY26AUG25000CE",

        "expiry":
            "2026-08-25",

        "strike":
            25000,

        "option_type":
            "CE",

        "side":
            "SELL",

        "lot_size":
            75,

        "entry_type":
            "MARKET",

        "entry_reference_price":
            120.0,

        "stop_loss_type":
            "PREMIUM_POINTS",

        "stop_loss_value":
            15.0,

        "target_value":
            None,

        "exit_mode":
            "STOP_LOSS_ONLY",

        "strategy_name":
            "TEST_STRATEGY",

        "strategy_id":
            "STRAT-001",

        "signal_id":
            "SIG-001",

        "notes":
            "TEST ONLY",

        "metadata":
            {
                "source": "UNIT_TEST"
            },
    }

    kwargs.update(
        overrides
    )

    return engine.create_plan(
        **kwargs
    )


# ============================================================
# TEST 1
# VALID SELL OPTION ORDER INTENT
# ============================================================

def test_valid_sell_intent():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "ALLOW",
        "Valid trade plan should be allowed",
    )

    assert_true(
        result["plan_allowed"],
        "Valid trade plan should be allowed",
    )

    assert_true(
        result["order_intent_created"],
        "Valid plan should create order intent",
    )

    assert_false(
        result["broker_order_allowed"],
        "TradePlanEngine must not receive "
        "broker execution authority",
    )

    intent = result["order_intent"]

    assert_equal(
        intent["side"],
        "SELL",
        "Order side mismatch",
    )

    assert_equal(
        intent["option_type"],
        "CE",
        "Option type mismatch",
    )

    assert_equal(
        intent["lots"],
        8,
        "Authorized lots mismatch",
    )

    assert_equal(
        intent["quantity"],
        600,
        "Authorized quantity mismatch",
    )

    assert_equal(
        intent["execution_status"],
        "INTENT_ONLY",
        "Execution status should remain INTENT_ONLY",
    )

    assert_false(
        intent["broker_order_allowed"],
        "Intent must not have broker authority",
    )

    print(
        "✅ PASS — Valid SELL option intent created"
    )


# ============================================================
# TEST 2
# BLOCKED ORCHESTRATOR
# ============================================================

def test_blocked_orchestrator():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        orchestrator_result=(
            blocked_orchestrator_result()
        ),
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Blocked orchestrator must block plan",
    )

    assert_false(
        result["order_intent_created"],
        "Blocked orchestrator must not create intent",
    )

    assert_equal(
        result["order_intent"],
        None,
        "Blocked result must contain no order intent",
    )

    assert_false(
        result["broker_order_allowed"],
        "Blocked plan must have no broker authority",
    )

    print(
        "✅ PASS — Upstream BLOCK cannot become "
        "an order intent"
    )


# ============================================================
# TEST 3
# ZERO AUTHORIZED POSITION
# ============================================================

def test_zero_authorized_position():

    engine = TradePlanEngine()

    upstream = approved_orchestrator_result()

    upstream["final_lots"] = 0
    upstream["final_quantity"] = 0

    result = create_standard_plan(
        engine,
        orchestrator_result=upstream,
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Zero authorized position must block",
    )

    assert_contains(
        result["validation_errors"],
        "NO_AUTHORIZED_LOTS",
        "Missing zero-lot validation",
    )

    assert_contains(
        result["validation_errors"],
        "NO_AUTHORIZED_QUANTITY",
        "Missing zero-quantity validation",
    )

    print(
        "✅ PASS — Zero authorized position blocked"
    )


# ============================================================
# TEST 4
# REQUESTED LOTS CANNOT EXCEED AUTHORITY
# ============================================================

def test_requested_lots_exceed_authority():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        requested_lots=9,
        requested_quantity=675,
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "9 lots must not exceed 8-lot authority",
    )

    assert_contains(
        result["validation_errors"],
        "REQUESTED_LOTS_EXCEED_AUTHORITY",
        "Missing lots authority violation",
    )

    assert_contains(
        result["validation_errors"],
        "REQUESTED_QUANTITY_EXCEEDS_AUTHORITY",
        "Missing quantity authority violation",
    )

    print(
        "✅ PASS — TradePlanEngine cannot "
        "increase authorized lots"
    )


# ============================================================
# TEST 5
# REQUESTED QUANTITY CANNOT EXCEED AUTHORITY
# ============================================================

def test_requested_quantity_exceeds_authority():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        requested_lots=8,
        requested_quantity=675,
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Quantity above 600 must block",
    )

    assert_contains(
        result["validation_errors"],
        "REQUESTED_QUANTITY_EXCEEDS_AUTHORITY",
        "Missing quantity authority violation",
    )

    print(
        "✅ PASS — Quantity cannot exceed "
        "orchestrator authority"
    )


# ============================================================
# TEST 6
# LOT / QUANTITY MISMATCH
# ============================================================

def test_lot_quantity_mismatch():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        requested_lots=4,
        requested_quantity=250,
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Invalid lot/quantity relationship "
        "must block",
    )

    assert_contains(
        result["validation_errors"],
        "LOT_QUANTITY_MISMATCH",
        "Missing lot/quantity mismatch",
    )

    print(
        "✅ PASS — Lots × lot size must equal quantity"
    )


# ============================================================
# TEST 7
# REDUCED POSITION IS ALLOWED
# ============================================================

def test_reduced_position():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        requested_lots=4,
        requested_quantity=300,
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "ALLOW",
        "Reducing authorized position should "
        "be allowed",
    )

    intent = result["order_intent"]

    assert_equal(
        intent["lots"],
        4,
        "Reduced lots incorrect",
    )

    assert_equal(
        intent["quantity"],
        300,
        "Reduced quantity incorrect",
    )

    assert_equal(
        intent["authorized_lots"],
        8,
        "Original authority must remain auditable",
    )

    assert_equal(
        intent["authorized_quantity"],
        600,
        "Original quantity authority must "
        "remain auditable",
    )

    print(
        "✅ PASS — Plan may reduce but never "
        "increase authorized position"
    )


# ============================================================
# TEST 8
# INVALID CONTRACT INFORMATION
# ============================================================

def test_invalid_contract():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        exchange="INVALID",
        tradingsymbol="",
        expiry="",
        strike=0,
        option_type="XX",
        side="HOLD",
    )

    print_result(result)

    expected_errors = [
        "INVALID_EXCHANGE",
        "TRADINGSYMBOL_REQUIRED",
        "EXPIRY_REQUIRED",
        "INVALID_STRIKE",
        "INVALID_OPTION_TYPE",
        "INVALID_SIDE",
    ]

    for expected in expected_errors:

        assert_contains(
            result["validation_errors"],
            expected,
            f"Missing validation error: {expected}",
        )

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Invalid contract must block plan",
    )

    print(
        "✅ PASS — Invalid option contract "
        "information rejected"
    )


# ============================================================
# TEST 9
# INVALID STOP LOSS
# ============================================================

def test_invalid_stop_loss():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        stop_loss_value=0,
    )

    print_result(result)

    assert_contains(
        result["validation_errors"],
        "INVALID_STOP_LOSS_VALUE",
        "Zero stop loss must be rejected",
    )

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Invalid stop loss must block plan",
    )

    print(
        "✅ PASS — Trade intent requires "
        "valid stop-loss protection"
    )


# ============================================================
# TEST 10
# LIMIT ORDER REQUIRES PRICE
# ============================================================

def test_limit_requires_price():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        entry_type="LIMIT",
        entry_reference_price=None,
    )

    print_result(result)

    assert_contains(
        result["validation_errors"],
        "LIMIT_PRICE_REQUIRED",
        "LIMIT order must require valid price",
    )

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "LIMIT order without price must block",
    )

    print(
        "✅ PASS — LIMIT order requires price"
    )


# ============================================================
# TEST 11
# STOP LOSS + TARGET REQUIRES TARGET
# ============================================================

def test_target_required():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        exit_mode="STOP_LOSS_TARGET",
        target_value=None,
    )

    print_result(result)

    assert_contains(
        result["validation_errors"],
        "TARGET_REQUIRED",
        "STOP_LOSS_TARGET mode requires target",
    )

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Missing target must block plan",
    )

    print(
        "✅ PASS — Target-based exit requires target"
    )


# ============================================================
# TEST 12
# AUTHORIZED RISK VIOLATION
# ============================================================

def test_risk_authority_violation():

    engine = TradePlanEngine()

    upstream = approved_orchestrator_result(
        authorized_risk=10000.0,
        estimated_max_loss=12000.0,
    )

    result = create_standard_plan(
        engine,
        orchestrator_result=upstream,
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Risk violation must block plan",
    )

    assert_contains(
        result["validation_errors"],
        "ESTIMATED_LOSS_EXCEEDS_AUTHORIZED_RISK",
        "Risk authority violation missing",
    )

    assert_false(
        result["order_intent_created"],
        "Risk violation must not create intent",
    )

    print(
        "✅ PASS — TradePlanEngine cannot accept "
        "risk above orchestrator authority"
    )


# ============================================================
# TEST 13
# AUTHORIZED LOT-SIZE CONSISTENCY
# ============================================================

def test_authorized_lot_size_mismatch():

    engine = TradePlanEngine()

    # Orchestrator says:
    # 8 lots = 600 quantity.
    #
    # But plan receives lot_size 50.
    # 8 × 50 = 400, not 600.
    #
    # Therefore contract cannot be trusted.

    result = create_standard_plan(
        engine,
        lot_size=50,
    )

    print_result(result)

    assert_contains(
        result["validation_errors"],
        "AUTHORIZED_POSITION_LOT_SIZE_MISMATCH",
        "Authorized lot-size mismatch "
        "must be detected",
    )

    assert_equal(
        result["plan_permission"],
        "BLOCK",
        "Lot-size inconsistency must block",
    )

    print(
        "✅ PASS — Contract lot size must match "
        "orchestrator-authorized position"
    )


# ============================================================
# TEST 14
# UNIQUE TRADE IDs
# ============================================================

def test_unique_trade_ids():

    engine = TradePlanEngine()

    result_1 = create_standard_plan(
        engine
    )

    result_2 = create_standard_plan(
        engine
    )

    assert_equal(
        result_1["plan_permission"],
        "ALLOW",
        "First plan should be allowed",
    )

    assert_equal(
        result_2["plan_permission"],
        "ALLOW",
        "Second plan should be allowed",
    )

    trade_id_1 = result_1["trade_id"]
    trade_id_2 = result_2["trade_id"]

    print(
        "Trade ID 1:",
        trade_id_1,
    )

    print(
        "Trade ID 2:",
        trade_id_2,
    )

    assert_true(
        bool(trade_id_1),
        "First trade ID missing",
    )

    assert_true(
        bool(trade_id_2),
        "Second trade ID missing",
    )

    if trade_id_1 == trade_id_2:

        raise AssertionError(
            "Trade IDs must be unique"
        )

    print(
        "✅ PASS — Every order intent receives "
        "a unique trade ID"
    )


# ============================================================
# TEST 15
# NORMALIZATION
# ============================================================

def test_normalization():

    engine = TradePlanEngine()

    result = create_standard_plan(
        engine,
        underlying=" nifty ",
        exchange=" nfo ",
        tradingsymbol=" nifty26aug25000ce ",
        option_type=" ce ",
        side=" sell ",
        entry_type=" market ",
        stop_loss_type=" premium_points ",
        exit_mode=" stop_loss_only ",
    )

    print_result(result)

    assert_equal(
        result["plan_permission"],
        "ALLOW",
        "Normalized values should be accepted",
    )

    intent = result["order_intent"]

    assert_equal(
        intent["underlying"],
        "NIFTY",
        "Underlying normalization failed",
    )

    assert_equal(
        intent["exchange"],
        "NFO",
        "Exchange normalization failed",
    )

    assert_equal(
        intent["option_type"],
        "CE",
        "Option type normalization failed",
    )

    assert_equal(
        intent["side"],
        "SELL",
        "Side normalization failed",
    )

    print(
        "✅ PASS — Contract/order text normalized safely"
    )


# ============================================================
# TEST 16
# BROKER EXECUTION AUTHORITY MUST NEVER EXIST
# ============================================================

def test_broker_authority_disabled():

    engine = TradePlanEngine()

    valid_result = create_standard_plan(
        engine
    )

    blocked_result = create_standard_plan(
        engine,
        orchestrator_result=(
            blocked_orchestrator_result()
        ),
    )

    assert_false(
        valid_result["broker_order_allowed"],
        "Valid plan unexpectedly received "
        "broker authority",
    )

    assert_false(
        valid_result[
            "order_intent"
        ]["broker_order_allowed"],
        "Valid intent unexpectedly received "
        "broker authority",
    )

    assert_false(
        blocked_result["broker_order_allowed"],
        "Blocked plan unexpectedly received "
        "broker authority",
    )

    assert_equal(
        valid_result[
            "order_intent"
        ]["broker_order_id"],
        None,
        "TradePlanEngine must not create "
        "broker order ID",
    )

    assert_equal(
        valid_result[
            "order_intent"
        ]["execution_status"],
        "INTENT_ONLY",
        "Trade plan must remain INTENT_ONLY",
    )

    print(
        "Broker Order Allowed : False"
    )

    print(
        "Execution Status     : INTENT_ONLY"
    )

    print(
        "Broker Order ID      : None"
    )

    print()
    print(
        "✅ PASS — TradePlanEngine has zero "
        "broker execution authority"
    )


# ============================================================
# TEST 17
# BLOCKED PLAN CAN NEVER CONTAIN AN ORDER INTENT
# ============================================================

def test_blocked_plan_invariant():

    engine = TradePlanEngine()

    scenarios = [
        {
            "name":
                "UPSTREAM_BLOCK",

            "kwargs": {
                "orchestrator_result":
                    blocked_orchestrator_result()
            },
        },
        {
            "name":
                "INVALID_STRIKE",

            "kwargs": {
                "strike": 0
            },
        },
        {
            "name":
                "INVALID_SIDE",

            "kwargs": {
                "side": "INVALID"
            },
        },
        {
            "name":
                "INVALID_STOP_LOSS",

            "kwargs": {
                "stop_loss_value": 0
            },
        },
        {
            "name":
                "EXCESS_LOTS",

            "kwargs": {
                "requested_lots": 9,
                "requested_quantity": 675,
            },
        },
    ]

    for scenario in scenarios:

        result = create_standard_plan(
            engine,
            **scenario["kwargs"],
        )

        assert_equal(
            result["plan_permission"],
            "BLOCK",
            f"{scenario['name']} should block",
        )

        assert_false(
            result["plan_allowed"],
            f"{scenario['name']} unexpectedly allowed",
        )

        assert_false(
            result["order_intent_created"],
            f"{scenario['name']} created an intent",
        )

        assert_equal(
            result["order_intent"],
            None,
            f"{scenario['name']} retained order intent",
        )

        assert_false(
            result["broker_order_allowed"],
            f"{scenario['name']} received broker authority",
        )

        print(
            "Verified:",
            scenario["name"],
            "→ BLOCK / NO INTENT / NO BROKER AUTHORITY",
        )

    print()
    print(
        "✅ PASS — Every blocked plan mathematically "
        "guarantees no order intent"
    )


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():

    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — VALID SELL OPTION INTENT",
            test_valid_sell_intent,
        ),
        (
            "TEST 2 — BLOCKED ORCHESTRATOR",
            test_blocked_orchestrator,
        ),
        (
            "TEST 3 — ZERO AUTHORIZED POSITION",
            test_zero_authorized_position,
        ),
        (
            "TEST 4 — LOT AUTHORITY CEILING",
            test_requested_lots_exceed_authority,
        ),
        (
            "TEST 5 — QUANTITY AUTHORITY CEILING",
            test_requested_quantity_exceeds_authority,
        ),
        (
            "TEST 6 — LOT / QUANTITY CONSISTENCY",
            test_lot_quantity_mismatch,
        ),
        (
            "TEST 7 — REDUCED POSITION",
            test_reduced_position,
        ),
        (
            "TEST 8 — CONTRACT VALIDATION",
            test_invalid_contract,
        ),
        (
            "TEST 9 — STOP-LOSS VALIDATION",
            test_invalid_stop_loss,
        ),
        (
            "TEST 10 — LIMIT PRICE VALIDATION",
            test_limit_requires_price,
        ),
        (
            "TEST 11 — TARGET VALIDATION",
            test_target_required,
        ),
        (
            "TEST 12 — RISK AUTHORITY",
            test_risk_authority_violation,
        ),
        (
            "TEST 13 — LOT-SIZE AUTHORITY",
            test_authorized_lot_size_mismatch,
        ),
        (
            "TEST 14 — UNIQUE TRADE IDs",
            test_unique_trade_ids,
        ),
        (
            "TEST 15 — NORMALIZATION",
            test_normalization,
        ),
        (
            "TEST 16 — BROKER AUTHORITY DISABLED",
            test_broker_authority_disabled,
        ),
        (
            "TEST 17 — UNIVERSAL BLOCK INVARIANT",
            test_blocked_plan_invariant,
        ),
    ]

    TOTAL = len(tests)

    heading(
        "THETA AI TRADER — "
        "TRADE PLAN ENGINE TEST SUITE"
    )

    print()
    print(
        "Broker Connection : NONE"
    )

    print(
        "Order Placement   : DISABLED"
    )

    print(
        "Test Type         : SAFETY / VALIDATION"
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
                f"❌ TRADE PLAN TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()

            raise

    print()
    line()

    print(
        f"✅ ALL TRADE PLAN ENGINE "
        f"TESTS PASSED ({PASSED}/{TOTAL})"
    )

    print(
        "🔒 POSITION AUTHORITY CANNOT BE INCREASED"
    )

    print(
        "🔒 BLOCKED PLANS CANNOT CREATE ORDER INTENTS"
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