# ============================================================
# THETA AI TRADER
# TRADE RISK ORCHESTRATOR — INTEGRATION TEST SUITE
# ============================================================

from config_manager import ConfigManager
from risk_management_engine import RiskManagementEngine
from risk_budget_allocator import RiskBudgetAllocator
from position_sizing_engine import PositionSizingEngine
from trade_risk_orchestrator import TradeRiskOrchestrator


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
        "Final Permission       :",
        result["final_permission"],
    )

    print(
        "Trade Allowed          :",
        result["trade_allowed"],
    )

    print(
        "Final Block Reason     :",
        result["final_block_reason"],
    )

    print(
        "Daily Risk Budget      :",
        result["daily_risk_budget_rupees"],
    )

    print(
        "Remaining Daily Risk   :",
        result["remaining_daily_risk_rupees"],
    )

    print(
        "Risk Engine Limit      :",
        result["risk_engine_limit_rupees"],
    )

    print(
        "Allocator Limit        :",
        result["allocator_limit_rupees"],
    )

    print(
        "Final Authorized Risk  :",
        result["final_authorized_risk_rupees"],
    )

    print(
        "Final Lots             :",
        result["final_lots"],
    )

    print(
        "Final Quantity         :",
        result["final_quantity"],
    )

    print(
        "Estimated Max Loss     :",
        result["estimated_max_loss"],
    )

    print(
        "Limiting Factor        :",
        result["limiting_factor"],
    )

    print(
        "Order Placement        :",
        result["order_placement_enabled"],
    )


# ============================================================
# ASSERTION HELPERS
# ============================================================

def assert_equal(
    actual,
    expected,
    message,
):

    if actual != expected:

        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


def assert_true(
    value,
    message,
):

    if value is not True:

        raise AssertionError(
            f"{message}\n"
            f"Expected: True\n"
            f"Actual  : {value}"
        )


def assert_false(
    value,
    message,
):

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


def assert_zero_position(
    result,
    message,
):

    if (
        result["final_lots"] != 0
        or result["final_quantity"] != 0
    ):

        raise AssertionError(
            f"{message}\n"
            f"Final Lots     : "
            f"{result['final_lots']}\n"
            f"Final Quantity : "
            f"{result['final_quantity']}"
        )


# ============================================================
# STANDARD VALID SIGNAL
# ============================================================

def valid_signal():

    return {
        "decision": "TRADE",
        "setup_valid": True,
        "direction": "BULLISH",
        "confidence": "HIGH",
        "trade_permission": "ALLOW",
        "signal_conflict": False,
    }


# ============================================================
# STANDARD ACCOUNT STATE
# ============================================================

def normal_account(
    capital=1000000.0,
):

    return {
        "daily_pnl": 0.0,
        "current_equity": capital,
        "peak_equity": capital,
        "consecutive_losses": 0,
        "open_positions": 0,
    }


# ============================================================
# STANDARD VOLATILITY STATE
# ============================================================

def normal_volatility():

    return {
        "spike_detected": False,
        "abnormal_candle": False,
        "rapid_move": False,
        "volatility_state": "NORMAL",
    }


# ============================================================
# STANDARD SESSION STATE
# ============================================================

def normal_session():

    return {
        "market_open": True,
        "new_entries_allowed": True,
    }


# ============================================================
# CREATE ISOLATED TEST ORCHESTRATOR
# ============================================================

def create_orchestrator(
    allocation_mode="FIXED",
    max_trades_per_day=3,
    confidence_scaling_enabled=True,
    minimum_setup_score=60.0,
    single_trade_cap_pct=40.0,
    max_risk_per_trade_pct=1.0,
    max_daily_loss_pct=3.0,
    expiry_risk_multiplier=0.50,
    max_lots=100,
    expiry_max_lots=100,
):

    # --------------------------------------------------------
    # ConfigManager is passed only because the orchestrator
    # requires a shared manager reference.
    #
    # All three test engines below use dynamic config OFF.
    #
    # Therefore this suite DOES NOT change dashboard/user
    # configuration.
    # --------------------------------------------------------

    config = ConfigManager()

    risk_engine = RiskManagementEngine(
        config_manager=config,
        use_dynamic_config=False,

        max_risk_per_trade_pct=(
            max_risk_per_trade_pct
        ),

        max_daily_loss_pct=(
            max_daily_loss_pct
        ),

        max_account_drawdown_pct=10.0,
        max_consecutive_losses=3,
        max_open_positions=3,
        caution_risk_multiplier=0.50,

        expiry_risk_multiplier=(
            expiry_risk_multiplier
        ),

        medium_confidence_multiplier=0.75,
        minimum_risk_multiplier=0.25,
    )

    allocator = RiskBudgetAllocator(
        config_manager=config,
        use_dynamic_config=False,

        allocation_mode=(
            allocation_mode
        ),

        max_trades_per_day=(
            max_trades_per_day
        ),

        confidence_scaling_enabled=(
            confidence_scaling_enabled
        ),

        minimum_setup_score=(
            minimum_setup_score
        ),

        max_single_trade_daily_risk_pct=(
            single_trade_cap_pct
        ),

        intelligent_reference_trades=3,

        confidence_multiplier_low=0.50,
        confidence_multiplier_medium=0.75,
        confidence_multiplier_high=1.00,
        confidence_multiplier_exceptional=1.25,
    )

    sizing_engine = PositionSizingEngine(
        config_manager=config,
        use_dynamic_config=False,

        # High test-only ceilings so the integration tests
        # measure risk logic instead of the old 10/5 defaults.
        default_max_lots=max_lots,
        default_expiry_max_lots=(
            expiry_max_lots
        ),
    )

    orchestrator = TradeRiskOrchestrator(
        config_manager=config,
        risk_engine=risk_engine,
        risk_budget_allocator=allocator,
        position_sizing_engine=sizing_engine,
        use_dynamic_config=False,
    )

    return orchestrator


# ============================================================
# STANDARD TRADE CALL
# ============================================================

def evaluate(
    orchestrator,
    capital=1000000.0,
    setup_score=90.0,
    lot_size=75,
    stop_loss_per_unit=10.0,
    margin_per_lot=10000.0,
    available_margin=1000000.0,
    account_state=None,
    volatility=None,
    session=None,
    trades_taken_today=0,
    is_expiry_day=False,
    remaining_daily_risk_rupees=None,
    signal_analysis=None,
):

    if account_state is None:
        account_state = normal_account(
            capital
        )

    if volatility is None:
        volatility = normal_volatility()

    if session is None:
        session = normal_session()

    if signal_analysis is None:
        signal_analysis = valid_signal()

    return orchestrator.analyze(
        capital=capital,

        signal_analysis=(
            signal_analysis
        ),

        setup_score=setup_score,

        lot_size=lot_size,

        stop_loss_per_unit=(
            stop_loss_per_unit
        ),

        margin_per_lot=(
            margin_per_lot
        ),

        available_margin=(
            available_margin
        ),

        account_state=(
            account_state
        ),

        volatility=volatility,
        session=session,

        trades_taken_today=(
            trades_taken_today
        ),

        is_expiry_day=(
            is_expiry_day
        ),

        remaining_daily_risk_rupees=(
            remaining_daily_risk_rupees
        ),
    )


# ============================================================
# TEST 1
# NORMAL COMPLETE PIPELINE
# ============================================================

def test_normal_trade():

    orchestrator = create_orchestrator()

    result = evaluate(
        orchestrator
    )

    print_result(result)

    assert_equal(
        result["final_permission"],
        "ALLOW",
        "Normal valid trade should be allowed",
    )

    assert_true(
        result["trade_allowed"],
        "Normal valid trade should be allowed",
    )

    assert_true(
        result["final_lots"] > 0,
        "Normal trade should receive lots",
    )

    assert_true(
        result["final_quantity"] > 0,
        "Normal trade should receive quantity",
    )

    print(
        "✅ PASS — Complete risk pipeline "
        "allowed valid trade"
    )


# ============================================================
# TEST 2
# RISK MANAGEMENT CEILING WINS
# ============================================================

def test_risk_engine_ceiling():

    orchestrator = create_orchestrator(
        allocation_mode="INTELLIGENT",
        single_trade_cap_pct=100.0,

        # 0.50% of ₹10,00,000 = ₹5,000
        max_risk_per_trade_pct=0.50,
    )

    result = evaluate(
        orchestrator,
        setup_score=97.0,
    )

    print_result(result)

    assert_close(
        result["risk_engine_limit_rupees"],
        5000.0,
        "Risk engine ceiling should be ₹5,000",
    )

    assert_true(
        result["allocator_limit_rupees"]
        > result["risk_engine_limit_rupees"],
        "Allocator should request more risk "
        "for this test",
    )

    assert_close(
        result[
            "final_authorized_risk_rupees"
        ],
        5000.0,
        "Risk engine ceiling must win",
    )

    print(
        "✅ PASS — RiskManagementEngine ceiling "
        "cannot be exceeded"
    )


# ============================================================
# TEST 3
# ALLOCATOR CEILING WINS
# ============================================================

def test_allocator_ceiling():

    orchestrator = create_orchestrator(
        allocation_mode="FIXED",

        # Risk engine permits 2% = ₹20,000.
        max_risk_per_trade_pct=2.0,

        # Daily risk = 3% = ₹30,000.
        # Fixed 3 trades = ₹10,000.
        max_daily_loss_pct=3.0,

        single_trade_cap_pct=100.0,
    )

    result = evaluate(
        orchestrator
    )

    print_result(result)

    assert_close(
        result["risk_engine_limit_rupees"],
        20000.0,
        "Risk engine should permit ₹20,000",
    )

    assert_close(
        result["allocator_limit_rupees"],
        10000.0,
        "Allocator should permit ₹10,000",
    )

    assert_close(
        result[
            "final_authorized_risk_rupees"
        ],
        10000.0,
        "Allocator ceiling must win",
    )

    print(
        "✅ PASS — RiskBudgetAllocator ceiling "
        "cannot be exceeded"
    )


# ============================================================
# TEST 4
# DAILY LOSS HARD BLOCK
# ============================================================

def test_daily_loss_block():

    orchestrator = create_orchestrator()

    account = normal_account()

    # 3% daily loss on ₹10,00,000.
    account["daily_pnl"] = -30000.0

    result = evaluate(
        orchestrator,
        account_state=account,
    )

    print_result(result)

    assert_equal(
        result["final_permission"],
        "BLOCK",
        "Daily loss limit should block trade",
    )

    assert_true(
        "DAILY_LOSS_LIMIT_REACHED"
        in result[
            "risk_management"
        ].get(
            "hard_blocks",
            [],
        ),
        "Daily-loss hard block missing",
    )

    assert_zero_position(
        result,
        "Daily loss block created a position",
    )

    print(
        "✅ PASS — Daily loss hard block "
        "propagated to zero quantity"
    )


# ============================================================
# TEST 5
# KILL SWITCH
# ============================================================

def test_kill_switch():

    orchestrator = create_orchestrator()

    orchestrator.risk_engine.activate_kill_switch(
        "INTEGRATION_TEST"
    )

    try:

        result = evaluate(
            orchestrator
        )

        print_result(result)

        assert_equal(
            result["final_permission"],
            "BLOCK",
            "Kill switch should block trade",
        )

        assert_true(
            "KILL_SWITCH_ACTIVE"
            in result[
                "risk_management"
            ].get(
                "hard_blocks",
                [],
            ),
            "Kill-switch hard block missing",
        )

        assert_zero_position(
            result,
            "Kill switch created a position",
        )

        print(
            "✅ PASS — Kill switch blocked "
            "entire downstream pipeline"
        )

    finally:

        orchestrator.risk_engine.reset_kill_switch()


# ============================================================
# TEST 6
# MAXIMUM TRADES PER DAY
# ============================================================

def test_max_trades():

    orchestrator = create_orchestrator(
        max_trades_per_day=3
    )

    result = evaluate(
        orchestrator,
        trades_taken_today=3,
    )

    print_result(result)

    assert_equal(
        result["final_permission"],
        "BLOCK",
        "Fourth trade should be blocked",
    )

    assert_equal(
        result[
            "risk_budget"
        ]["reason"],
        "MAX_TRADES_PER_DAY_REACHED",
        "Wrong allocator block reason",
    )

    assert_zero_position(
        result,
        "Maximum-trade block created position",
    )

    print(
        "✅ PASS — Maximum trade-attempt "
        "limit propagated correctly"
    )


# ============================================================
# TEST 7
# LOW SETUP SCORE
# ============================================================

def test_low_setup_score():

    orchestrator = create_orchestrator(
        allocation_mode="INTELLIGENT",
        minimum_setup_score=60.0,
    )

    result = evaluate(
        orchestrator,
        setup_score=55.0,
    )

    print_result(result)

    assert_equal(
        result["final_permission"],
        "BLOCK",
        "Weak setup should be blocked",
    )

    assert_equal(
        result[
            "risk_budget"
        ]["reason"],
        "SETUP_SCORE_BELOW_MINIMUM",
        "Wrong weak-setup block reason",
    )

    assert_zero_position(
        result,
        "Weak setup created position",
    )

    print(
        "✅ PASS — Weak setup receives "
        "zero position authority"
    )


# ============================================================
# TEST 8
# INSUFFICIENT MARGIN
# ============================================================

def test_insufficient_margin():

    orchestrator = create_orchestrator()

    result = evaluate(
        orchestrator,

        margin_per_lot=10000.0,

        # Less than one lot.
        available_margin=5000.0,
    )

    print_result(result)

    assert_true(
        result[
            "final_authorized_risk_rupees"
        ] > 0,
        "Risk should be authorized before "
        "margin sizing",
    )

    assert_equal(
        result["final_permission"],
        "BLOCK",
        "Insufficient margin should block position",
    )

    assert_equal(
        result[
            "position_sizing"
        ]["reason"],
        "INSUFFICIENT_MARGIN_FOR_ONE_LOT",
        "Wrong margin block reason",
    )

    assert_zero_position(
        result,
        "Insufficient margin created position",
    )

    print(
        "✅ PASS — Margin layer can veto "
        "an otherwise valid trade"
    )


# ============================================================
# TEST 9
# RISK TOO SMALL FOR ONE LOT
# ============================================================

def test_risk_too_small_for_one_lot():

    orchestrator = create_orchestrator(
        max_risk_per_trade_pct=0.10,
    )

    # Risk engine:
    # 0.10% of ₹10,00,000 = ₹1,000.
    #
    # Risk per lot:
    # 75 × ₹20 = ₹1,500.
    #
    # Therefore zero lots.

    result = evaluate(
        orchestrator,
        stop_loss_per_unit=20.0,
    )

    print_result(result)

    assert_close(
        result[
            "final_authorized_risk_rupees"
        ],
        1000.0,
        "Expected ₹1,000 authorized risk",
    )

    assert_equal(
        result[
            "position_sizing"
        ]["reason"],
        "RISK_BUDGET_TOO_SMALL_FOR_ONE_LOT",
        "Wrong small-risk block reason",
    )

    assert_zero_position(
        result,
        "Risk smaller than one lot created position",
    )

    print(
        "✅ PASS — Risk smaller than one lot "
        "produces zero quantity"
    )


# ============================================================
# TEST 10
# FINAL MAX LOSS INVARIANT
# ============================================================

def test_final_max_loss_invariant():

    orchestrator = create_orchestrator(
        max_risk_per_trade_pct=1.0
    )

    result = evaluate(
        orchestrator,
        stop_loss_per_unit=17.0,
    )

    print_result(result)

    assert_true(
        result["trade_allowed"],
        "Trade should be allowed",
    )

    assert_true(
        result["estimated_max_loss"]
        <= result[
            "final_authorized_risk_rupees"
        ],
        "Estimated maximum loss exceeded "
        "authorized risk",
    )

    assert_true(
        result["estimated_max_loss"]
        <= result[
            "remaining_daily_risk_rupees"
        ],
        "Estimated maximum loss exceeded "
        "remaining daily risk",
    )

    print(
        "✅ PASS — Final position max loss "
        "stays inside authorized risk"
    )


# ============================================================
# TEST 11
# REMAINING DAILY RISK CEILING
# ============================================================

def test_remaining_daily_risk():

    orchestrator = create_orchestrator(
        allocation_mode="INTELLIGENT",
        single_trade_cap_pct=100.0,

        # Let RiskManagementEngine permit enough risk
        # for remaining budget to become the true ceiling.
        max_risk_per_trade_pct=5.0,
    )

    result = evaluate(
        orchestrator,
        setup_score=100.0,

        # Only ₹6,000 risk remains today.
        remaining_daily_risk_rupees=6000.0,
    )

    print_result(result)

    assert_close(
        result[
            "remaining_daily_risk_rupees"
        ],
        6000.0,
        "Remaining daily risk incorrect",
    )

    assert_close(
        result[
            "allocator_limit_rupees"
        ],
        6000.0,
        "Allocator should be capped at ₹6,000",
    )

    assert_close(
        result[
            "final_authorized_risk_rupees"
        ],
        6000.0,
        "Final risk should be capped at ₹6,000",
    )

    assert_true(
        result[
            "final_authorized_risk_rupees"
        ]
        <= result[
            "remaining_daily_risk_rupees"
        ],
        "Confidence bypassed remaining daily risk",
    )

    print(
        "✅ PASS — Exceptional confidence cannot "
        "bypass remaining daily risk"
    )


# ============================================================
# TEST 12
# EXPIRY DAY PROPAGATION
# ============================================================

def test_expiry_day():

    orchestrator = create_orchestrator(
        allocation_mode="FIXED",
        max_risk_per_trade_pct=1.0,

        # Risk engine cuts risk to 50% on expiry.
        expiry_risk_multiplier=0.50,

        # Keep test sizing cap high enough not to interfere.
        max_lots=100,
        expiry_max_lots=100,
    )

    result = evaluate(
        orchestrator,
        is_expiry_day=True,
    )

    print_result(result)

    assert_true(
        result["is_expiry_day"],
        "Final result lost expiry state",
    )

    assert_true(
        result[
            "position_sizing"
        ]["is_expiry_day"],
        "PositionSizingEngine lost expiry state",
    )

    assert_true(
        "EXPIRY_DAY_RISK_REDUCTION"
        in result[
            "risk_management"
        ].get(
            "risk_reductions",
            [],
        ),
        "Risk engine did not apply "
        "expiry risk reduction",
    )

    assert_close(
        result[
            "risk_engine_limit_rupees"
        ],
        5000.0,
        "Expiry should reduce ₹10,000 "
        "risk ceiling to ₹5,000",
    )

    assert_true(
        result[
            "final_authorized_risk_rupees"
        ] <= 5000.0,
        "Expiry risk reduction was bypassed",
    )

    print(
        "✅ PASS — Expiry state propagated "
        "through complete pipeline"
    )


# ============================================================
# TEST 13
# FULL AUDIT TRAIL
# ============================================================

def test_audit_trail():

    orchestrator = create_orchestrator()

    result = evaluate(
        orchestrator
    )

    print_result(result)

    assert_true(
        isinstance(
            result["risk_management"],
            dict,
        ),
        "Risk-management audit trail missing",
    )

    assert_true(
        isinstance(
            result["risk_budget"],
            dict,
        ),
        "Risk-budget audit trail missing",
    )

    assert_true(
        isinstance(
            result["position_sizing"],
            dict,
        ),
        "Position-sizing audit trail missing",
    )

    assert_true(
        "hard_blocks"
        in result["risk_management"],
        "Risk hard-block audit data missing",
    )

    assert_true(
        "approved_risk_rupees"
        in result["risk_budget"],
        "Allocator audit data missing",
    )

    assert_true(
        "estimated_max_loss"
        in result["position_sizing"],
        "Sizing audit data missing",
    )

    print(
        "✅ PASS — Complete three-engine "
        "audit trail retained"
    )


# ============================================================
# TEST 14
# ORDER PLACEMENT MUST ALWAYS BE DISABLED
# ============================================================

def test_order_placement_disabled():

    orchestrator = create_orchestrator()

    result = evaluate(
        orchestrator
    )

    print_result(result)

    assert_false(
        result["order_placement_enabled"],
        "Risk orchestrator must never place orders",
    )

    print(
        "✅ PASS — Orchestrator has "
        "zero order-placement authority"
    )


# ============================================================
# TEST 15
# UNIVERSAL UPSTREAM BLOCK INVARIANT
# ============================================================

def test_upstream_block_zero_quantity():

    orchestrator = create_orchestrator()

    scenarios = []

    # --------------------------------------------------------
    # SIGNAL BLOCK
    # --------------------------------------------------------

    blocked_signal = valid_signal()
    blocked_signal["trade_permission"] = "BLOCK"

    scenarios.append(
        (
            "SIGNAL_PERMISSION_BLOCK",
            {
                "signal_analysis":
                    blocked_signal,
            },
        )
    )

    # --------------------------------------------------------
    # DAILY LOSS BLOCK
    # --------------------------------------------------------

    daily_loss_account = normal_account()
    daily_loss_account["daily_pnl"] = -30000.0

    scenarios.append(
        (
            "DAILY_LOSS_LIMIT_REACHED",
            {
                "account_state":
                    daily_loss_account,
            },
        )
    )

    # --------------------------------------------------------
    # DRAWDOWN BLOCK
    # --------------------------------------------------------

    drawdown_account = normal_account()
    drawdown_account["peak_equity"] = 1000000.0
    drawdown_account["current_equity"] = 900000.0

    scenarios.append(
        (
            "ACCOUNT_DRAWDOWN_LIMIT_REACHED",
            {
                "account_state":
                    drawdown_account,
            },
        )
    )

    # --------------------------------------------------------
    # CONSECUTIVE LOSS BLOCK
    # --------------------------------------------------------

    loss_account = normal_account()
    loss_account["consecutive_losses"] = 3

    scenarios.append(
        (
            "CONSECUTIVE_LOSS_LIMIT_REACHED",
            {
                "account_state":
                    loss_account,
            },
        )
    )

    # --------------------------------------------------------
    # OPEN POSITION BLOCK
    # --------------------------------------------------------

    position_account = normal_account()
    position_account["open_positions"] = 3

    scenarios.append(
        (
            "MAX_OPEN_POSITIONS_REACHED",
            {
                "account_state":
                    position_account,
            },
        )
    )

    # --------------------------------------------------------
    # VOLATILITY BLOCK
    # --------------------------------------------------------

    dangerous_volatility = normal_volatility()
    dangerous_volatility[
        "spike_detected"
    ] = True

    scenarios.append(
        (
            "ACTIVE_PRICE_SPIKE",
            {
                "volatility":
                    dangerous_volatility,
            },
        )
    )

    # --------------------------------------------------------
    # SESSION BLOCK
    # --------------------------------------------------------

    blocked_session = normal_session()
    blocked_session[
        "new_entries_allowed"
    ] = False

    scenarios.append(
        (
            "SESSION_ENTRY_BLOCK",
            {
                "session":
                    blocked_session,
            },
        )
    )

    # --------------------------------------------------------
    # RUN EVERY BLOCK SCENARIO
    # --------------------------------------------------------

    for expected_block, kwargs in scenarios:

        result = evaluate(
            orchestrator,
            **kwargs,
        )

        assert_equal(
            result["final_permission"],
            "BLOCK",
            f"{expected_block} did not "
            f"block final permission",
        )

        assert_zero_position(
            result,
            f"{expected_block} produced "
            f"non-zero position",
        )

        assert_close(
            result[
                "final_authorized_risk_rupees"
            ],
            0.0,
            f"{expected_block} retained "
            f"risk authority",
        )

        assert_true(
            expected_block
            in result[
                "risk_management"
            ].get(
                "hard_blocks",
                [],
            ),
            f"{expected_block} missing from "
            f"risk audit trail",
        )

        print(
            "Verified:",
            expected_block,
            "→ BLOCK / 0 lots / 0 quantity",
        )

    print()
    print(
        "✅ PASS — Every upstream hard block "
        "mathematically guarantees zero position"
    )


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():

    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — NORMAL COMPLETE PIPELINE",
            test_normal_trade,
        ),
        (
            "TEST 2 — RISK ENGINE CEILING WINS",
            test_risk_engine_ceiling,
        ),
        (
            "TEST 3 — ALLOCATOR CEILING WINS",
            test_allocator_ceiling,
        ),
        (
            "TEST 4 — DAILY LOSS HARD BLOCK",
            test_daily_loss_block,
        ),
        (
            "TEST 5 — MANUAL KILL SWITCH",
            test_kill_switch,
        ),
        (
            "TEST 6 — MAXIMUM TRADES PER DAY",
            test_max_trades,
        ),
        (
            "TEST 7 — LOW SETUP SCORE",
            test_low_setup_score,
        ),
        (
            "TEST 8 — INSUFFICIENT MARGIN",
            test_insufficient_margin,
        ),
        (
            "TEST 9 — RISK TOO SMALL FOR ONE LOT",
            test_risk_too_small_for_one_lot,
        ),
        (
            "TEST 10 — FINAL MAX-LOSS INVARIANT",
            test_final_max_loss_invariant,
        ),
        (
            "TEST 11 — REMAINING DAILY RISK CEILING",
            test_remaining_daily_risk,
        ),
        (
            "TEST 12 — EXPIRY-DAY PROPAGATION",
            test_expiry_day,
        ),
        (
            "TEST 13 — COMPLETE AUDIT TRAIL",
            test_audit_trail,
        ),
        (
            "TEST 14 — ORDER PLACEMENT DISABLED",
            test_order_placement_disabled,
        ),
        (
            "TEST 15 — UNIVERSAL BLOCK INVARIANT",
            test_upstream_block_zero_quantity,
        ),
    ]

    TOTAL = len(tests)

    heading(
        "THETA AI TRADER — "
        "TRADE RISK ORCHESTRATOR TEST SUITE"
    )

    print()
    print(
        "Configuration Mode : ISOLATED"
    )

    print(
        "Dynamic Config     : DISABLED FOR TESTS"
    )

    print(
        "User Config Changes: NONE"
    )

    print(
        "Order Placement    : DISABLED"
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
                f"❌ ORCHESTRATOR TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()

            raise

    print()
    line()

    print(
        f"✅ ALL TRADE RISK ORCHESTRATOR "
        f"TESTS PASSED ({PASSED}/{TOTAL})"
    )

    print(
        "🔒 ALL TESTS USED ISOLATED CONFIGURATION"
    )

    print(
        "🔒 USER DASHBOARD CONFIGURATION "
        "WAS NOT MODIFIED"
    )

    print(
        "🔒 ORDER PLACEMENT DISABLED"
    )

    line()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_all_tests()