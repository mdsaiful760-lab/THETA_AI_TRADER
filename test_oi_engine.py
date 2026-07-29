# ============================================================
# THETA AI TRADER — OI ENGINE TEST
# ============================================================

from oi_engine import OIEngine


engine = OIEngine()


print("=" * 70)
print("🧪 THETA AI TRADER — OI ENGINE TEST")
print("=" * 70)


# ============================================================
# PRICE + OI CLASSIFICATION TESTS
# ============================================================

tests = [
    (
        "LONG BUILDUP",
        100,
        102,
        100000,
        110000,
        "LONG_BUILDUP",
    ),
    (
        "SHORT BUILDUP",
        100,
        98,
        100000,
        110000,
        "SHORT_BUILDUP",
    ),
    (
        "LONG UNWINDING",
        100,
        98,
        100000,
        90000,
        "LONG_UNWINDING",
    ),
    (
        "SHORT COVERING",
        100,
        102,
        100000,
        90000,
        "SHORT_COVERING",
    ),
    (
        "NEUTRAL",
        100,
        100.02,
        100000,
        100500,
        "NEUTRAL",
    ),
]


print("\nPRICE + OI CLASSIFICATION")
print("-" * 70)


for (
    name,
    previous_price,
    current_price,
    previous_oi,
    current_oi,
    expected,
) in tests:

    result = engine.classify(
        previous_price=previous_price,
        current_price=current_price,
        previous_oi=previous_oi,
        current_oi=current_oi,
    )

    actual = result["classification"]

    assert actual == expected, (
        f"{name} failed: "
        f"expected {expected}, got {actual}"
    )

    print(
        f"✅ {name:<18} → {actual}"
    )


# ============================================================
# OPTION INTERPRETATION TESTS
# ============================================================

interpretation_tests = [
    (
        "CE",
        "SHORT_BUILDUP",
        "RESISTANCE_STRENGTHENING",
    ),
    (
        "CE",
        "SHORT_COVERING",
        "RESISTANCE_WEAKENING",
    ),
    (
        "PE",
        "SHORT_BUILDUP",
        "SUPPORT_STRENGTHENING",
    ),
    (
        "PE",
        "SHORT_COVERING",
        "SUPPORT_WEAKENING",
    ),
]


print("\nOPTION-SIDE INTERPRETATION")
print("-" * 70)


for (
    option_type,
    classification,
    expected,
) in interpretation_tests:

    result = engine.interpret_option_activity(
        option_type,
        classification,
    )

    actual = result["interpretation"]

    assert actual == expected, (
        f"{option_type} {classification} failed: "
        f"expected {expected}, got {actual}"
    )

    print(
        f"✅ {option_type} "
        f"{classification:<15} → {actual}"
    )


print("\n" + "=" * 70)
print("✅ ALL OI ENGINE TESTS PASSED")
print("🔒 TEST ONLY — NO ORDER PLACEMENT")
print("=" * 70)