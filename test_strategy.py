from strategy_engine import StrategyEngine


engine = StrategyEngine()


# ============================================================
# TEST 1 — CURRENT-LIKE RANGE-BOUND MARKET
# ============================================================

range_market = {
    "regime": "RANGE_BOUND",
    "confidence": 85,
    "adx_state": "RANGE",
    "ema_state": "BEARISH",
    "vix_state": "NORMAL_VOL",
    "atr_state": "LOW_ATR",
    "regime_ready": True,
    "trading_permission": "ALLOWED",
}

result = engine.select_strategy(range_market)

print("=" * 60)
print("🤖 THETA AI TRADER — STRATEGY ENGINE TEST")
print("=" * 60)

print("\nTEST 1 — RANGE BOUND")
print(f"Strategy   : {result['strategy']}")
print(f"Action     : {result['action']}")
print(f"Confidence : {result['confidence']}%")

print("Reasons:")
for reason in result["reasons"]:
    print(f"• {reason}")


# ============================================================
# TEST 2 — MORNING WARM-UP
# ============================================================

warmup_market = range_market.copy()
warmup_market["regime_ready"] = False
warmup_market["trading_permission"] = "NO NEW TRADE"

result = engine.select_strategy(warmup_market)

print("\n" + "=" * 60)
print("TEST 2 — MORNING WARM-UP")
print("=" * 60)

print(f"Strategy   : {result['strategy']}")
print(f"Action     : {result['action']}")
print(f"Confidence : {result['confidence']}%")

print("Reasons:")
for reason in result["reasons"]:
    print(f"• {reason}")


# ============================================================
# TEST 3 — TRANSITION MARKET
# ============================================================

transition_market = range_market.copy()
transition_market["regime"] = "TRANSITION"
transition_market["confidence"] = 60
transition_market["adx_state"] = "TRANSITION"

result = engine.select_strategy(transition_market)

print("\n" + "=" * 60)
print("TEST 3 — TRANSITION")
print("=" * 60)

print(f"Strategy   : {result['strategy']}")
print(f"Action     : {result['action']}")
print(f"Confidence : {result['confidence']}%")

print("Reasons:")
for reason in result["reasons"]:
    print(f"• {reason}")


# ============================================================
# TEST 4 — BULLISH TREND
# ============================================================

bullish_market = range_market.copy()
bullish_market["regime"] = "BULLISH_TREND"
bullish_market["adx_state"] = "TREND"
bullish_market["ema_state"] = "BULLISH"
bullish_market["atr_state"] = "NORMAL_ATR"

result = engine.select_strategy(bullish_market)

print("\n" + "=" * 60)
print("TEST 4 — BULLISH TREND")
print("=" * 60)

print(f"Strategy   : {result['strategy']}")
print(f"Action     : {result['action']}")
print(f"Confidence : {result['confidence']}%")

print("Reasons:")
for reason in result["reasons"]:
    print(f"• {reason}")


# ============================================================
# TEST 5 — BEARISH TREND
# ============================================================

bearish_market = bullish_market.copy()
bearish_market["regime"] = "BEARISH_TREND"
bearish_market["ema_state"] = "BEARISH"

result = engine.select_strategy(bearish_market)

print("\n" + "=" * 60)
print("TEST 5 — BEARISH TREND")
print("=" * 60)

print(f"Strategy   : {result['strategy']}")
print(f"Action     : {result['action']}")
print(f"Confidence : {result['confidence']}%")

print("Reasons:")
for reason in result["reasons"]:
    print(f"• {reason}")
