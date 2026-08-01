# ============================================================
# THETA AI TRADER
# MARKET DATA ADAPTER — TEST SUITE
# ============================================================

from datetime import date, datetime

from market_data_adapter import MarketDataAdapter


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


def print_chain_result(result):
    print("Adapter Permission  :", result["adapter_permission"])
    print("Adapter Allowed     :", result["adapter_allowed"])
    print("Reason              :", result["reason"])
    print("Underlying          :", result["underlying"])
    print("Expiry              :", result["expiry"])
    print("Exchange            :", result["exchange"])
    print("Instrument Count    :", result["instrument_count"])
    print("Matched Instruments :", result["matched_instruments"])
    print("Normalized Count    :", result["normalized_count"])
    print("Rejected Count      :", result["rejected_count"])
    print("Broker Order Allowed:", result["broker_order_allowed"])
    print("Validation Errors   :", result["validation_errors"])


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


def assert_contains(collection, expected, message):
    if expected not in collection:
        raise AssertionError(
            f"{message}\n"
            f"Expected item: {expected}\n"
            f"Actual       : {collection}"
        )


def assert_close(actual, expected, message, tolerance=0.000001):
    if abs(float(actual) - float(expected)) > tolerance:
        raise AssertionError(
            f"{message}\n"
            f"Expected: {expected}\n"
            f"Actual  : {actual}"
        )


# ============================================================
# ZERODHA-SHAPED INSTRUMENT FACTORY
# ============================================================

def make_instrument(
    symbol,
    strike,
    option_type,
    expiry=date(2026, 8, 25),
    underlying="NIFTY",
    exchange="NFO",
    lot_size=75,
    instrument_token=100001,
    exchange_token=200001,
    tick_size=0.05,
):
    return {
        "instrument_token": instrument_token,
        "exchange_token": exchange_token,
        "tradingsymbol": symbol,
        "name": underlying,
        "last_price": 0.0,
        "expiry": expiry,
        "strike": strike,
        "tick_size": tick_size,
        "lot_size": lot_size,
        "instrument_type": option_type,
        "segment": f"{exchange}-OPT",
        "exchange": exchange,
    }


# ============================================================
# ZERODHA-SHAPED QUOTE FACTORY
# ============================================================

def make_quote(
    ltp=100.0,
    bid=99.5,
    ask=100.5,
    volume=100000,
    oi=200000,
    timestamp=datetime(2026, 8, 20, 10, 30, 0),
):
    return {
        "instrument_token": 100001,
        "timestamp": timestamp,
        "last_trade_time": timestamp,
        "last_price": ltp,
        "last_quantity": 75,
        "buy_quantity": 10000,
        "sell_quantity": 9000,
        "volume": volume,
        "average_price": 98.75,
        "oi": oi,
        "oi_day_high": oi + 10000,
        "oi_day_low": max(0, oi - 10000),
        "depth": {
            "buy": [
                {
                    "quantity": 750,
                    "price": bid - 0.50,
                    "orders": 2,
                },
                {
                    "quantity": 1500,
                    "price": bid,
                    "orders": 5,
                },
                {
                    "quantity": 500,
                    "price": bid - 1.00,
                    "orders": 1,
                },
            ],
            "sell": [
                {
                    "quantity": 1500,
                    "price": ask,
                    "orders": 4,
                },
                {
                    "quantity": 500,
                    "price": ask + 1.00,
                    "orders": 2,
                },
                {
                    "quantity": 750,
                    "price": ask + 0.50,
                    "orders": 3,
                },
            ],
        },
    }


# ============================================================
# STANDARD INSTRUMENT MASTER
# ============================================================

def standard_instruments():
    return [
        make_instrument(
            "NIFTY26AUG24900CE",
            24900,
            "CE",
            instrument_token=101,
            exchange_token=201,
        ),
        make_instrument(
            "NIFTY26AUG24900PE",
            24900,
            "PE",
            instrument_token=102,
            exchange_token=202,
        ),
        make_instrument(
            "NIFTY26AUG25000CE",
            25000,
            "CE",
            instrument_token=103,
            exchange_token=203,
        ),
        make_instrument(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            instrument_token=104,
            exchange_token=204,
        ),
        make_instrument(
            "NIFTY26AUG25100CE",
            25100,
            "CE",
            instrument_token=105,
            exchange_token=205,
        ),
        make_instrument(
            "NIFTY26AUG25100PE",
            25100,
            "PE",
            instrument_token=106,
            exchange_token=206,
        ),
    ]


def standard_quotes():
    return {
        "NFO:NIFTY26AUG24900CE": make_quote(
            ltp=180,
            bid=179.5,
            ask=180.5,
            oi=300000,
        ),
        "NFO:NIFTY26AUG24900PE": make_quote(
            ltp=80,
            bid=79.5,
            ask=80.5,
            oi=250000,
        ),
        "NFO:NIFTY26AUG25000CE": make_quote(
            ltp=130,
            bid=129.5,
            ask=130.5,
            oi=500000,
        ),
        "NFO:NIFTY26AUG25000PE": make_quote(
            ltp=110,
            bid=109.5,
            ask=110.5,
            oi=550000,
        ),
        "NFO:NIFTY26AUG25100CE": make_quote(
            ltp=90,
            bid=89.5,
            ask=90.5,
            oi=400000,
        ),
        "NFO:NIFTY26AUG25100PE": make_quote(
            ltp=155,
            bid=154.5,
            ask=155.5,
            oi=420000,
        ),
    }


# ============================================================
# TEST 1 — INSTRUMENT NORMALIZATION
# ============================================================

def test_instrument_normalization():
    adapter = MarketDataAdapter()

    instrument = make_instrument(
        " nifty26aug25000pe ",
        25000,
        " pe ",
        underlying=" nifty ",
        exchange=" nfo ",
        instrument_token=123456,
        exchange_token=654321,
    )

    result = adapter.normalize_instrument(instrument)

    assert_true(result["valid"], "Instrument should normalize")

    normalized = result["instrument"]

    assert_equal(normalized["underlying"], "NIFTY",
                 "Underlying normalization failed")
    assert_equal(normalized["exchange"], "NFO",
                 "Exchange normalization failed")
    assert_equal(normalized["tradingsymbol"], "NIFTY26AUG25000PE",
                 "Symbol normalization failed")
    assert_equal(normalized["option_type"], "PE",
                 "Option type normalization failed")
    assert_equal(normalized["expiry"], "2026-08-25",
                 "Expiry normalization failed")
    assert_close(normalized["strike"], 25000.0,
                 "Strike normalization failed")
    assert_equal(normalized["lot_size"], 75,
                 "Lot-size normalization failed")
    assert_equal(
        normalized["quote_key"],
        "NFO:NIFTY26AUG25000PE",
        "Quote key incorrect",
    )

    print("Normalized Symbol :", normalized["tradingsymbol"])
    print("Quote Key         :", normalized["quote_key"])
    print("Expiry            :", normalized["expiry"])
    print("Lot Size          :", normalized["lot_size"])
    print("✅ PASS — Zerodha instrument normalized correctly")


# ============================================================
# TEST 2 — BEST BID / ASK EXTRACTION
# ============================================================

def test_best_bid_ask():
    adapter = MarketDataAdapter()

    quote = make_quote(
        ltp=100,
        bid=99.50,
        ask=100.50,
    )

    result = adapter.normalize_quote(quote)

    assert_true(result["valid"], "Quote should be valid")

    normalized = result["quote"]

    assert_close(normalized["bid"], 99.50,
                 "Best bid extraction failed")
    assert_close(normalized["ask"], 100.50,
                 "Best ask extraction failed")

    print("Best Bid :", normalized["bid"])
    print("Best Ask :", normalized["ask"])
    print("✅ PASS — Best market-depth bid/ask extracted")


# ============================================================
# TEST 3 — LTP / VOLUME / OI MAPPING
# ============================================================

def test_market_fields():
    adapter = MarketDataAdapter()

    result = adapter.normalize_quote(
        make_quote(
            ltp=123.45,
            volume=456789,
            oi=987654,
        )
    )

    quote = result["quote"]

    assert_close(quote["ltp"], 123.45, "LTP mapping failed")
    assert_equal(quote["volume"], 456789, "Volume mapping failed")
    assert_equal(quote["open_interest"], 987654, "OI mapping failed")

    print("LTP    :", quote["ltp"])
    print("Volume :", quote["volume"])
    print("OI     :", quote["open_interest"])
    print("✅ PASS — LTP / volume / OI mapped correctly")


# ============================================================
# TEST 4 — EXPIRY NORMALIZATION
# ============================================================

def test_expiry_normalization():
    adapter = MarketDataAdapter()

    values = [
        date(2026, 8, 25),
        datetime(2026, 8, 25, 12, 30),
        "2026-08-25",
        "25-08-2026",
        "2026/08/25",
        "25/08/2026",
    ]

    for value in values:
        normalized = adapter._normalize_expiry(value)

        assert_equal(
            normalized,
            "2026-08-25",
            f"Expiry normalization failed for {value}",
        )

        print(value, "→", normalized)

    print("✅ PASS — Expiry formats normalize consistently")


# ============================================================
# TEST 5 — BUILD ONE CONTRACT
# ============================================================

def test_build_contract():
    adapter = MarketDataAdapter()

    instrument = make_instrument(
        "NIFTY26AUG25000PE",
        25000,
        "PE",
        instrument_token=999,
        exchange_token=888,
    )

    quote = make_quote(
        ltp=110,
        bid=109.5,
        ask=110.5,
        volume=200000,
        oi=500000,
    )

    greeks = {
        "delta": -0.18,
        "iv": 14.5,
        "gamma": 0.001,
        "theta": -8.5,
        "vega": 4.2,
    }

    result = adapter.build_contract(
        instrument=instrument,
        quote=quote,
        greeks=greeks,
    )

    assert_true(result["valid"], "Contract should normalize")

    contract = result["contract"]

    assert_equal(contract["tradingsymbol"], "NIFTY26AUG25000PE",
                 "Wrong symbol")
    assert_close(contract["ltp"], 110, "Wrong LTP")
    assert_close(contract["bid"], 109.5, "Wrong bid")
    assert_close(contract["ask"], 110.5, "Wrong ask")
    assert_equal(contract["open_interest"], 500000, "Wrong OI")
    assert_close(contract["delta"], -0.18, "Wrong delta")
    assert_close(contract["iv"], 14.5, "Wrong IV")

    print("Symbol :", contract["tradingsymbol"])
    print("LTP    :", contract["ltp"])
    print("Bid    :", contract["bid"])
    print("Ask    :", contract["ask"])
    print("Delta  :", contract["delta"])
    print("IV     :", contract["iv"])
    print("✅ PASS — Complete normalized contract built")


# ============================================================
# TEST 6 — COMPLETE OPTION CHAIN
# ============================================================

def test_complete_chain():
    adapter = MarketDataAdapter()

    result = adapter.build_option_chain(
        instruments=standard_instruments(),
        quotes=standard_quotes(),
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_chain_result(result)

    assert_equal(result["adapter_permission"], "ALLOW",
                 "Valid chain should be allowed")
    assert_true(result["adapter_allowed"],
                "Adapter should allow valid chain")
    assert_equal(result["normalized_count"], 6,
                 "All six contracts should normalize")
    assert_equal(result["rejected_count"], 0,
                 "No valid contracts should reject")

    print("✅ PASS — Complete option chain normalized")


# ============================================================
# TEST 7 — MISSING QUOTE REJECTION
# ============================================================

def test_missing_quote():
    adapter = MarketDataAdapter()

    instruments = [
        make_instrument(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
        )
    ]

    result = adapter.build_option_chain(
        instruments=instruments,
        quotes={},
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_chain_result(result)

    assert_equal(result["adapter_permission"], "BLOCK",
                 "Missing quote must block")
    assert_equal(result["normalized_count"], 0,
                 "No contract should normalize")
    assert_equal(result["rejected_count"], 1,
                 "Missing quote should reject one contract")
    assert_equal(
        result["rejections"][0]["reason"],
        "QUOTE_NOT_FOUND",
        "Wrong missing-quote reason",
    )

    print("✅ PASS — Missing live quote rejected")


# ============================================================
# TEST 8 — MALFORMED INSTRUMENT
# ============================================================

def test_malformed_instrument():
    adapter = MarketDataAdapter()

    malformed = make_instrument(
        "",
        0,
        "XX",
        lot_size=0,
        instrument_token=None,
    )

    result = adapter.normalize_instrument(malformed)

    assert_false(result["valid"],
                 "Malformed instrument must be invalid")

    errors = result["errors"]

    assert_contains(errors, "MISSING_TRADINGSYMBOL",
                    "Missing symbol error absent")
    assert_contains(errors, "INVALID_OPTION_TYPE",
                    "Option type error absent")
    assert_contains(errors, "INVALID_STRIKE",
                    "Strike error absent")
    assert_contains(errors, "INVALID_LOT_SIZE",
                    "Lot-size error absent")
    assert_contains(errors, "MISSING_INSTRUMENT_TOKEN",
                    "Instrument-token error absent")

    print("Errors:", errors)
    print("✅ PASS — Malformed instrument rejected safely")


# ============================================================
# TEST 9 — INVALID MARKET DEPTH
# ============================================================

def test_invalid_market_depth():
    adapter = MarketDataAdapter()

    quote = make_quote()

    quote["depth"] = {
        "buy": [],
        "sell": [],
    }

    result = adapter.normalize_quote(quote)

    assert_false(result["valid"],
                 "Missing depth must invalidate quote")

    assert_contains(result["errors"], "MISSING_BID",
                    "Missing bid error absent")
    assert_contains(result["errors"], "MISSING_ASK",
                    "Missing ask error absent")

    print("Errors:", result["errors"])
    print("✅ PASS — Missing market depth rejected")


# ============================================================
# TEST 10 — INVERTED MARKET
# ============================================================

def test_inverted_market():
    adapter = MarketDataAdapter()

    quote = make_quote(
        bid=105,
        ask=100,
    )

    result = adapter.normalize_quote(quote)

    assert_false(result["valid"],
                 "Inverted market must be invalid")

    assert_contains(
        result["errors"],
        "INVERTED_MARKET",
        "Inverted-market protection absent",
    )

    print("Errors:", result["errors"])
    print("✅ PASS — Inverted bid/ask market rejected")


# ============================================================
# TEST 11 — DUPLICATE INSTRUMENT PROTECTION
# ============================================================

def test_duplicate_instrument():
    adapter = MarketDataAdapter()

    first = make_instrument(
        "NIFTY26AUG25000PE",
        25000,
        "PE",
        instrument_token=1001,
    )

    duplicate = make_instrument(
        "NIFTY26AUG25000PE",
        25000,
        "PE",
        instrument_token=1002,
    )

    quotes = {
        "NFO:NIFTY26AUG25000PE": make_quote()
    }

    result = adapter.build_option_chain(
        instruments=[first, duplicate],
        quotes=quotes,
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_chain_result(result)

    assert_equal(result["normalized_count"], 1,
                 "Duplicate symbol must not normalize twice")
    assert_equal(result["rejected_count"], 1,
                 "Duplicate should produce rejection")

    assert_equal(
        result["rejections"][0]["reason"],
        "DUPLICATE_INSTRUMENT",
        "Wrong duplicate reason",
    )

    print("✅ PASS — Duplicate instrument protected")


# ============================================================
# TEST 12 — OPTIONAL GREEKS ATTACHMENT
# ============================================================

def test_greeks_attachment():
    adapter = MarketDataAdapter()

    instruments = [
        make_instrument(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            instrument_token=777,
        )
    ]

    quotes = {
        "NFO:NIFTY26AUG25000PE": make_quote()
    }

    greeks_map = {
        "NFO:NIFTY26AUG25000PE": {
            "delta": -0.19,
            "iv": 16.25,
            "gamma": 0.002,
            "theta": -7.5,
            "vega": 4.5,
        }
    }

    result = adapter.build_option_chain(
        instruments=instruments,
        quotes=quotes,
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
        greeks_map=greeks_map,
    )

    contract = result["contracts"][0]

    assert_close(contract["delta"], -0.19,
                 "Delta attachment failed")
    assert_close(contract["iv"], 16.25,
                 "IV attachment failed")
    assert_close(contract["gamma"], 0.002,
                 "Gamma attachment failed")
    assert_close(contract["theta"], -7.5,
                 "Theta attachment failed")
    assert_close(contract["vega"], 4.5,
                 "Vega attachment failed")

    print("Delta :", contract["delta"])
    print("IV    :", contract["iv"])
    print("Gamma :", contract["gamma"])
    print("Theta :", contract["theta"])
    print("Vega  :", contract["vega"])
    print("✅ PASS — Optional Greeks attached correctly")


# ============================================================
# TEST 13 — GREEKS MAY BE ABSENT
# ============================================================

def test_missing_greeks_allowed():
    adapter = MarketDataAdapter()

    result = adapter.build_contract(
        instrument=make_instrument(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
        ),
        quote=make_quote(),
        greeks=None,
    )

    assert_true(
        result["valid"],
        "Adapter must normalize market data without Greeks",
    )

    contract = result["contract"]

    assert_equal(contract["delta"], None,
                 "Adapter must not invent delta")
    assert_equal(contract["iv"], None,
                 "Adapter must not invent IV")
    assert_equal(contract["gamma"], None,
                 "Adapter must not invent gamma")
    assert_equal(contract["theta"], None,
                 "Adapter must not invent theta")
    assert_equal(contract["vega"], None,
                 "Adapter must not invent vega")

    print("Delta :", contract["delta"])
    print("IV    :", contract["iv"])
    print("✅ PASS — Missing Greeks remain None; nothing fabricated")


# ============================================================
# TEST 14 — AVAILABLE EXPIRIES / NEAREST EXPIRY
# ============================================================

def test_expiry_discovery():
    adapter = MarketDataAdapter()

    instruments = [
        make_instrument(
            "OLD",
            24000,
            "PE",
            expiry=date(2026, 8, 18),
            instrument_token=1,
        ),
        make_instrument(
            "NEAR",
            25000,
            "PE",
            expiry=date(2026, 8, 25),
            instrument_token=2,
        ),
        make_instrument(
            "FAR",
            26000,
            "PE",
            expiry=date(2026, 9, 1),
            instrument_token=3,
        ),
    ]

    expiries = adapter.get_available_expiries(
        instruments=instruments,
        underlying="NIFTY",
        exchange="NFO",
        reference_date=date(2026, 8, 20),
    )

    nearest = adapter.get_nearest_expiry(
        instruments=instruments,
        underlying="NIFTY",
        exchange="NFO",
        reference_date=date(2026, 8, 20),
    )

    assert_equal(
        expiries,
        [
            date(2026, 8, 25),
            date(2026, 9, 1),
        ],
        "Future expiry discovery incorrect",
    )

    assert_equal(
        nearest,
        date(2026, 8, 25),
        "Nearest expiry incorrect",
    )

    print("Available Expiries :", expiries)
    print("Nearest Expiry     :", nearest)
    print("✅ PASS — Expiry discovery is dynamic")


# ============================================================
# TEST 15 — AVAILABLE STRIKES / ATM
# ============================================================

def test_strikes_and_atm():
    adapter = MarketDataAdapter()

    instruments = standard_instruments()

    strikes = adapter.get_available_strikes(
        instruments=instruments,
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
    )

    atm = adapter.get_atm_strike(
        instruments=instruments,
        underlying="NIFTY",
        expiry="2026-08-25",
        spot_price=25037,
        exchange="NFO",
    )

    assert_equal(
        strikes,
        [24900.0, 25000.0, 25100.0],
        "Strike discovery incorrect",
    )

    assert_close(
        atm,
        25000.0,
        "ATM detection incorrect",
    )

    print("Strikes :", strikes)
    print("ATM     :", atm)
    print("✅ PASS — Available strikes and ATM detected dynamically")


# ============================================================
# TEST 16 — STRIKE STEP
# ============================================================

def test_strike_step():
    adapter = MarketDataAdapter()

    step = adapter.detect_strike_step(
        [
            24800,
            24900,
            25000,
            25100,
            25200,
        ]
    )

    assert_close(
        step,
        100,
        "Strike-step detection incorrect",
    )

    print("Detected Strike Step :", step)
    print("✅ PASS — Strike interval detected dynamically")


# ============================================================
# TEST 17 — NEARBY STRIKES
# ============================================================

def test_nearby_strikes():
    adapter = MarketDataAdapter()

    strikes = [
        24500,
        24600,
        24700,
        24800,
        24900,
        25000,
        25100,
        25200,
        25300,
        25400,
        25500,
    ]

    nearby = adapter.get_nearby_strikes(
        strikes=strikes,
        spot_price=25020,
        strikes_each_side=2,
    )

    assert_equal(
        nearby,
        [
            24800.0,
            24900.0,
            25000.0,
            25100.0,
            25200.0,
        ],
        "Nearby-strike window incorrect",
    )

    print("Nearby Strikes :", nearby)
    print("✅ PASS — ATM-centered strike window generated")


# ============================================================
# TEST 18 — DETERMINISTIC ORDERING
# ============================================================

def test_deterministic_ordering():
    adapter = MarketDataAdapter()

    instruments = [
        make_instrument(
            "NIFTY26AUG25100PE",
            25100,
            "PE",
            instrument_token=1,
        ),
        make_instrument(
            "NIFTY26AUG24900PE",
            24900,
            "PE",
            instrument_token=2,
        ),
        make_instrument(
            "NIFTY26AUG25000PE",
            25000,
            "PE",
            instrument_token=3,
        ),
    ]

    quotes = {
        "NFO:NIFTY26AUG25100PE": make_quote(),
        "NFO:NIFTY26AUG24900PE": make_quote(),
        "NFO:NIFTY26AUG25000PE": make_quote(),
    }

    result = adapter.build_option_chain(
        instruments=instruments,
        quotes=quotes,
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
        option_types=["PE"],
    )

    symbols = [
        contract["tradingsymbol"]
        for contract in result["contracts"]
    ]

    expected = [
        "NIFTY26AUG24900PE",
        "NIFTY26AUG25000PE",
        "NIFTY26AUG25100PE",
    ]

    assert_equal(
        symbols,
        expected,
        "Option-chain ordering must be deterministic",
    )

    print("Ordered Symbols:")
    for symbol in symbols:
        print(" ", symbol)

    print("✅ PASS — Normalized contracts ordered deterministically")


# ============================================================
# TEST 19 — REQUEST VALIDATION
# ============================================================

def test_request_validation():
    adapter = MarketDataAdapter()

    result = adapter.build_option_chain(
        instruments=[],
        quotes={},
        underlying="",
        expiry="2026-08-25",
        exchange="INVALID",
        option_types=["XX"],
    )

    print_chain_result(result)

    assert_equal(
        result["adapter_permission"],
        "BLOCK",
        "Invalid request must block",
    )

    assert_false(
        result["adapter_allowed"],
        "Invalid request cannot be allowed",
    )

    assert_contains(
        result["validation_errors"],
        "UNDERLYING_REQUIRED",
        "Missing-underlying validation absent",
    )

    assert_contains(
        result["validation_errors"],
        "INVALID_EXCHANGE",
        "Invalid-exchange validation absent",
    )

    assert_contains(
        result["validation_errors"],
        "INVALID_OPTION_TYPES",
        "Invalid-option-type validation absent",
    )

    print("✅ PASS — Invalid adapter request blocked")


# ============================================================
# TEST 20 — ZERO EXECUTION / RISK AUTHORITY
# ============================================================

def test_zero_authority():
    adapter = MarketDataAdapter()

    result = adapter.build_option_chain(
        instruments=standard_instruments(),
        quotes=standard_quotes(),
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
    )

    assert_false(
        result["broker_order_allowed"],
        "MarketDataAdapter must never receive broker authority",
    )

    forbidden_result_fields = [
        "final_lots",
        "final_quantity",
        "approved_risk",
        "authorized_risk",
        "final_authorized_risk_rupees",
        "daily_risk_budget_rupees",
        "remaining_daily_risk_rupees",
        "order_id",
    ]

    forbidden_contract_fields = [
        "final_lots",
        "final_quantity",
        "approved_risk",
        "authorized_risk",
        "final_authorized_risk_rupees",
        "order_id",
    ]

    for field in forbidden_result_fields:
        if field in result:
            raise AssertionError(
                "MarketDataAdapter illegally contains "
                f"execution/risk authority: {field}"
            )

    for contract in result["contracts"]:
        for field in forbidden_contract_fields:
            if field in contract:
                raise AssertionError(
                    "Normalized market contract illegally contains "
                    f"execution/risk authority: {field}"
                )

    print("Market Data Authority : YES")
    print("Contract Selection    : NO")
    print("Risk Allocation       : NO")
    print("Position Sizing       : NO")
    print("Broker Execution      : NO")
    print()
    print(
        "✅ PASS — MarketDataAdapter has zero "
        "risk/position/execution authority"
    )


# ============================================================
# TEST 21 — NO VALID CONTRACTS = BLOCK
# ============================================================

def test_no_valid_contracts():
    adapter = MarketDataAdapter()

    instrument = make_instrument(
        "NIFTY26AUG25000PE",
        25000,
        "PE",
    )

    bad_quote = make_quote()
    bad_quote["depth"] = {
        "buy": [],
        "sell": [],
    }

    result = adapter.build_option_chain(
        instruments=[instrument],
        quotes={
            "NFO:NIFTY26AUG25000PE": bad_quote
        },
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
    )

    print_chain_result(result)

    assert_equal(
        result["adapter_permission"],
        "BLOCK",
        "No valid contracts must block",
    )

    assert_false(
        result["adapter_allowed"],
        "Adapter must not allow empty valid chain",
    )

    assert_equal(
        result["reason"],
        "NO_VALID_OPTION_CONTRACTS",
        "Wrong block reason",
    )

    assert_equal(
        result["contracts"],
        [],
        "Blocked adapter must expose no normalized contracts",
    )

    assert_false(
        result["broker_order_allowed"],
        "Blocked adapter cannot have broker authority",
    )

    print(
        "✅ PASS — Invalid market data cannot flow "
        "downstream as a valid chain"
    )


# ============================================================
# TEST 22 — SELECTOR-COMPATIBLE OUTPUT
# ============================================================

def test_selector_compatibility():
    adapter = MarketDataAdapter()

    greeks_map = {}

    for instrument in standard_instruments():
        key = (
            f'{instrument["exchange"]}:'
            f'{instrument["tradingsymbol"]}'
        )

        if instrument["instrument_type"] == "CE":
            delta = 0.18
        else:
            delta = -0.18

        greeks_map[key] = {
            "delta": delta,
            "iv": 15.0,
        }

    result = adapter.build_option_chain(
        instruments=standard_instruments(),
        quotes=standard_quotes(),
        underlying="NIFTY",
        expiry="2026-08-25",
        exchange="NFO",
        greeks_map=greeks_map,
    )

    required_fields = [
        "underlying",
        "exchange",
        "tradingsymbol",
        "expiry",
        "strike",
        "option_type",
        "lot_size",
        "ltp",
        "bid",
        "ask",
        "volume",
        "open_interest",
        "delta",
        "iv",
        "instrument_token",
        "exchange_token",
        "tick_size",
    ]

    for contract in result["contracts"]:
        for field in required_fields:
            if field not in contract:
                raise AssertionError(
                    "OptionContractSelector-required field "
                    f"missing: {field}"
                )

    print("Normalized Contracts :", len(result["contracts"]))
    print("Required Fields      :", len(required_fields))
    print()
    print(
        "✅ PASS — MarketDataAdapter output is compatible "
        "with OptionContractSelector"
    )


# ============================================================
# TEST RUNNER
# ============================================================

def run_all_tests():
    global PASSED
    global TOTAL

    tests = [
        (
            "TEST 1 — INSTRUMENT NORMALIZATION",
            test_instrument_normalization,
        ),
        (
            "TEST 2 — BEST BID / ASK",
            test_best_bid_ask,
        ),
        (
            "TEST 3 — MARKET FIELD MAPPING",
            test_market_fields,
        ),
        (
            "TEST 4 — EXPIRY NORMALIZATION",
            test_expiry_normalization,
        ),
        (
            "TEST 5 — BUILD CONTRACT",
            test_build_contract,
        ),
        (
            "TEST 6 — COMPLETE OPTION CHAIN",
            test_complete_chain,
        ),
        (
            "TEST 7 — MISSING QUOTE",
            test_missing_quote,
        ),
        (
            "TEST 8 — MALFORMED INSTRUMENT",
            test_malformed_instrument,
        ),
        (
            "TEST 9 — INVALID MARKET DEPTH",
            test_invalid_market_depth,
        ),
        (
            "TEST 10 — INVERTED MARKET",
            test_inverted_market,
        ),
        (
            "TEST 11 — DUPLICATE PROTECTION",
            test_duplicate_instrument,
        ),
        (
            "TEST 12 — GREEKS ATTACHMENT",
            test_greeks_attachment,
        ),
        (
            "TEST 13 — MISSING GREEKS",
            test_missing_greeks_allowed,
        ),
        (
            "TEST 14 — EXPIRY DISCOVERY",
            test_expiry_discovery,
        ),
        (
            "TEST 15 — STRIKES / ATM",
            test_strikes_and_atm,
        ),
        (
            "TEST 16 — STRIKE STEP",
            test_strike_step,
        ),
        (
            "TEST 17 — NEARBY STRIKES",
            test_nearby_strikes,
        ),
        (
            "TEST 18 — DETERMINISTIC ORDERING",
            test_deterministic_ordering,
        ),
        (
            "TEST 19 — REQUEST VALIDATION",
            test_request_validation,
        ),
        (
            "TEST 20 — ZERO AUTHORITY",
            test_zero_authority,
        ),
        (
            "TEST 21 — NO VALID CONTRACTS",
            test_no_valid_contracts,
        ),
        (
            "TEST 22 — SELECTOR COMPATIBILITY",
            test_selector_compatibility,
        ),
    ]

    TOTAL = len(tests)

    heading(
        "THETA AI TRADER — MARKET DATA ADAPTER TEST SUITE"
    )

    print()
    print("Live Zerodha Calls : NONE")
    print("Real Orders        : NONE")
    print("Broker Execution   : DISABLED")
    print("Test Data          : SIMULATED KITE-SHAPED DATA")

    for title, test in tests:
        heading(title)

        try:
            test()
            PASSED += 1

        except Exception as error:
            print()
            print("❌ TEST FAILED")
            print("Test :", test.__name__)
            print("Error:", error)
            print()
            line()

            print(
                f"❌ MARKET DATA ADAPTER TESTS FAILED "
                f"({PASSED}/{TOTAL} passed)"
            )

            line()
            raise

    print()
    line()

    print(
        f"✅ ALL MARKET DATA ADAPTER TESTS PASSED "
        f"({PASSED}/{TOTAL})"
    )

    print("🔒 ADAPTER NORMALIZES MARKET DATA ONLY")
    print("🔒 NO CONTRACT-SELECTION AUTHORITY")
    print("🔒 NO RISK/POSITION-SIZING AUTHORITY")
    print("🔒 NO BROKER ORDER AUTHORITY")

    line()


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    run_all_tests()