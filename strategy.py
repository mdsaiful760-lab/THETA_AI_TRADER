# ============================================================
# THETA AI TRADER
# STRATEGY ENGINE - VERSION 1
# ============================================================

TARGET_DELTA = 0.15
MIN_DELTA = 0.10
MAX_DELTA = 0.20


def select_option_by_delta(options, option_type):
    """
    Select the option closest to TARGET_DELTA.

    CE delta should be positive.
    PE delta should be negative.
    """

    candidates = []

    for option in options:

        delta = option["delta"]

        # Use absolute delta for comparison
        abs_delta = abs(delta)

        if MIN_DELTA <= abs_delta <= MAX_DELTA:
            candidates.append(option)

    if not candidates:
        return None

    # Find option closest to target delta
    selected = min(
        candidates,
        key=lambda x: abs(abs(x["delta"]) - TARGET_DELTA)
    )

    return selected


def generate_short_strangle_signal(options):

    ce_options = [
        option for option in options
        if option["type"] == "CE"
    ]

    pe_options = [
        option for option in options
        if option["type"] == "PE"
    ]

    selected_ce = select_option_by_delta(
        ce_options,
        "CE"
    )

    selected_pe = select_option_by_delta(
        pe_options,
        "PE"
    )

    return selected_ce, selected_pe


# ============================================================
# TEST DATA
# ============================================================

if __name__ == "__main__":

    print("=" * 65)
    print("🚀 THETA AI TRADER — STRATEGY ENGINE TEST")
    print("=" * 65)

    # Temporary test data only
    test_options = [

        {
            "strike": 24000,
            "type": "CE",
            "delta": 0.22,
            "premium": 42.55
        },

        {
            "strike": 24050,
            "type": "CE",
            "delta": 0.18,
            "premium": 32.30
        },

        {
            "strike": 24100,
            "type": "CE",
            "delta": 0.14,
            "premium": 23.85
        },

        {
            "strike": 24150,
            "type": "CE",
            "delta": 0.10,
            "premium": 17.95
        },

        {
            "strike": 23500,
            "type": "PE",
            "delta": -0.11,
            "premium": 20.00
        },

        {
            "strike": 23550,
            "type": "PE",
            "delta": -0.15,
            "premium": 25.00
        },

        {
            "strike": 23600,
            "type": "PE",
            "delta": -0.19,
            "premium": 31.00
        }
    ]

    ce, pe = generate_short_strangle_signal(test_options)

    print("\n🎯 TARGET DELTA:", TARGET_DELTA)
    print(
        f"Allowed Delta Range: "
        f"{MIN_DELTA:.2f} - {MAX_DELTA:.2f}"
    )

    print("\n" + "-" * 65)

    if ce:
        print("📕 CE CANDIDATE")
        print(f"Strike  : {ce['strike']}")
        print(f"Delta   : {ce['delta']}")
        print(f"Premium : {ce['premium']}")
    else:
        print("❌ No suitable CE found")

    print()

    if pe:
        print("📗 PE CANDIDATE")
        print(f"Strike  : {pe['strike']}")
        print(f"Delta   : {pe['delta']}")
        print(f"Premium : {pe['premium']}")
    else:
        print("❌ No suitable PE found")

    print("\n" + "=" * 65)
    print("🔒 SIGNAL ONLY — NO ORDER PLACEMENT")
    print("=" * 65)