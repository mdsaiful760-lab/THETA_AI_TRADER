# ============================================================
# THETA AI TRADER — POSITION SIZING ENGINE TEST SUITE
# ============================================================

from position_sizing_engine import PositionSizingEngine


def heading(title):
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def allowed_risk(risk_rupees=10000.0):
    return {
        "risk_permission": "ALLOW",
        "entry_allowed": True,
        "allowed_risk_rupees": risk_rupees,
    }


def blocked_risk():
    return {
        "risk_permission": "BLOCK",
        "entry_allowed": False,
        "allowed_risk_rupees": 0.0,
    }


def print_result(result):
    print("Sizing Permission    :", result["sizing_permission"])
    print("Position Allowed     :", result["position_allowed"])
    print("Reason               :", result["reason"])
    print("Final Lots           :", result["final_lots"])
    print("Final Quantity       :", result["final_quantity"])
    print("Allowed Risk Rs      :", result["allowed_risk_rupees"])
    print("Risk Per Lot         :", result["risk_per_lot"])
    print("Lots By Risk         :", result["lots_by_risk"])
    print("Lots By Margin       :", result["lots_by_margin"])
    print("Lots By Config       :", result["lots_by_config"])
    print("Estimated Margin     :", result["estimated_margin_required"])
    print("Estimated Max Loss   :", result["estimated_max_loss"])
    print("Limiting Factor      :", result["limiting_factor"])


def test_risk_limited():
    heading("TEST 1 — RISK-LIMITED POSITION")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=allowed_risk(10000),
        lot_size=75,
        stop_loss_per_unit=20,
        margin_per_lot=100000,
        available_margin=1000000,
        is_expiry_day=False,
    )

    print_result(result)

    # Risk per lot = 75 × Rs 20 = Rs 1,500
    # Rs 10,000 // Rs 1,500 = 6 lots
    assert result["final_lots"] == 6
    assert result["final_quantity"] == 450
    assert result["estimated_max_loss"] == 9000
    assert "RISK_BUDGET" in result["limiting_factor"]

    print("✅ PASS — Risk budget correctly limited position")


def test_margin_limited():
    heading("TEST 2 — MARGIN-LIMITED POSITION")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=allowed_risk(50000),
        lot_size=75,
        stop_loss_per_unit=20,
        margin_per_lot=100000,
        available_margin=300000,
        is_expiry_day=False,
    )

    print_result(result)

    # Margin allows only 3 lots.
    assert result["final_lots"] == 3
    assert result["final_quantity"] == 225
    assert "AVAILABLE_MARGIN" in result["limiting_factor"]

    print("✅ PASS — Available margin correctly limited position")


def test_config_limited():
    heading("TEST 3 — MAX LOT CONFIGURATION")

    engine = PositionSizingEngine(
        default_max_lots=4
    )

    result = engine.analyze(
        risk_analysis=allowed_risk(100000),
        lot_size=75,
        stop_loss_per_unit=10,
        margin_per_lot=50000,
        available_margin=1000000,
        is_expiry_day=False,
    )

    print_result(result)

    assert result["final_lots"] == 4
    assert result["final_quantity"] == 300
    assert "MAX_LOT_LIMIT" in result["limiting_factor"]

    print("✅ PASS — Maximum lot protection applied")


def test_expiry_limit():
    heading("TEST 4 — EXPIRY-DAY LOT RESTRICTION")

    engine = PositionSizingEngine(
        default_max_lots=10,
        default_expiry_max_lots=2,
    )

    result = engine.analyze(
        risk_analysis=allowed_risk(100000),
        lot_size=75,
        stop_loss_per_unit=10,
        margin_per_lot=50000,
        available_margin=1000000,
        is_expiry_day=True,
    )

    print_result(result)

    assert result["final_lots"] == 2
    assert result["final_quantity"] == 150
    assert result["active_max_lots"] == 2
    assert "EXPIRY_LOT_LIMIT" in result["limiting_factor"]

    print("✅ PASS — Expiry-day lot restriction applied")


def test_risk_too_small():
    heading("TEST 5 — RISK BUDGET TOO SMALL")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=allowed_risk(1000),
        lot_size=75,
        stop_loss_per_unit=20,
        margin_per_lot=100000,
        available_margin=1000000,
    )

    print_result(result)

    assert result["position_allowed"] is False
    assert result["final_lots"] == 0
    assert result["final_quantity"] == 0
    assert result["reason"] == "RISK_BUDGET_TOO_SMALL_FOR_ONE_LOT"

    print("✅ PASS — Trade blocked when one lot exceeds risk budget")


def test_margin_too_small():
    heading("TEST 6 — INSUFFICIENT MARGIN")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=allowed_risk(10000),
        lot_size=75,
        stop_loss_per_unit=20,
        margin_per_lot=100000,
        available_margin=50000,
    )

    print_result(result)

    assert result["position_allowed"] is False
    assert result["final_lots"] == 0
    assert result["reason"] == "INSUFFICIENT_MARGIN_FOR_ONE_LOT"

    print("✅ PASS — Trade blocked when margin is insufficient")


def test_upstream_block():
    heading("TEST 7 — UPSTREAM RISK BLOCK")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=blocked_risk(),
        lot_size=75,
        stop_loss_per_unit=20,
        margin_per_lot=100000,
        available_margin=1000000,
    )

    print_result(result)

    assert result["sizing_permission"] == "BLOCK"
    assert result["position_allowed"] is False
    assert result["final_lots"] == 0
    assert result["final_quantity"] == 0
    assert result["reason"] == "UPSTREAM_RISK_BLOCK"

    print("✅ PASS — RiskManagementEngine block respected")


def test_invalid_stop_loss():
    heading("TEST 8 — INVALID STOP LOSS")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=allowed_risk(),
        lot_size=75,
        stop_loss_per_unit=0,
        margin_per_lot=100000,
        available_margin=1000000,
    )

    print_result(result)

    assert result["position_allowed"] is False
    assert result["reason"] == "INVALID_STOP_LOSS"

    print("✅ PASS — Invalid stop loss rejected")


def test_invalid_lot_size():
    heading("TEST 9 — INVALID LOT SIZE")

    engine = PositionSizingEngine()

    result = engine.analyze(
        risk_analysis=allowed_risk(),
        lot_size=0,
        stop_loss_per_unit=20,
        margin_per_lot=100000,
        available_margin=1000000,
    )

    print_result(result)

    assert result["position_allowed"] is False
    assert result["reason"] == "INVALID_LOT_SIZE"

    print("✅ PASS — Invalid lot size rejected")


def test_final_loss_never_exceeds_budget():
    heading("TEST 10 — FINAL MAX-LOSS SAFETY")

    engine = PositionSizingEngine(
        default_max_lots=100
    )

    result = engine.analyze(
        risk_analysis=allowed_risk(10000),
        lot_size=75,
        stop_loss_per_unit=17,
        margin_per_lot=10000,
        available_margin=1000000,
    )

    print_result(result)

    assert result["position_allowed"] is True

    assert (
        result["estimated_max_loss"]
        <= result["allowed_risk_rupees"]
    )

    print("✅ PASS — Estimated max loss stays inside risk budget")


def run_all_tests():
    tests = [
        test_risk_limited,
        test_margin_limited,
        test_config_limited,
        test_expiry_limit,
        test_risk_too_small,
        test_margin_too_small,
        test_upstream_block,
        test_invalid_stop_loss,
        test_invalid_lot_size,
        test_final_loss_never_exceeds_budget,
    ]

    passed = 0

    heading(
        "🧪 THETA AI TRADER — POSITION SIZING ENGINE TEST SUITE"
    )

    for test in tests:
        try:
            test()
            passed += 1

        except Exception as error:
            print()
            print("❌ TEST FAILED")
            print("Test :", test.__name__)
            print("Error:", error)

            print()
            print("=" * 76)
            print(
                f"❌ POSITION SIZING TESTS FAILED "
                f"({passed}/{len(tests)} passed)"
            )
            print("=" * 76)

            raise

    print()
    print("=" * 76)
    print(
        f"✅ ALL POSITION SIZING ENGINE TESTS PASSED "
        f"({passed}/{len(tests)})"
    )
    print("🔒 TEST ONLY — NO ORDER PLACEMENT")
    print("=" * 76)


if __name__ == "__main__":
    run_all_tests()

