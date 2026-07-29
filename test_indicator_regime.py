# ============================================================
# THETA AI TRADER
# INDICATOR ENGINE → REGIME ENGINE INTEGRATION TEST
# ============================================================

from indicator_engine import IndicatorEngine
from regime_engine import RegimeEngine


print("=" * 70)
print("🧪 THETA AI TRADER — INDICATOR / REGIME INTEGRATION TEST")
print("=" * 70)


# ============================================================
# CREATE ENGINES
# ============================================================

indicator_engine = IndicatorEngine()

regime_engine = RegimeEngine()


# ============================================================
# SYNTHETIC BULLISH MARKET DATA
# ============================================================

candles = []

for i in range(50):

    candles.append({
        "open": 24000 + (i * 10),
        "high": 24020 + (i * 10),
        "low": 23990 + (i * 10),
        "close": 24010 + (i * 10),
    })


# ============================================================
# RUN INDICATOR ENGINE
# ============================================================

indicators = indicator_engine.analyze(
    candles
)


print("\n📊 INDICATOR ENGINE")

print("-" * 70)

print(
    f"Close       : "
    f"{indicators['close']:.2f}"
)

print(
    f"Fast EMA    : "
    f"{indicators['fast_ema']:.2f}"
)

print(
    f"Slow EMA    : "
    f"{indicators['slow_ema']:.2f}"
)

print(
    f"EMA State   : "
    f"{indicators['ema_structure']}"
)

print(
    f"RSI         : "
    f"{indicators['rsi']:.2f}"
)

print(
    f"ATR         : "
    f"{indicators['atr']:.2f}"
)

print(
    f"ADX         : "
    f"{indicators['adx']:.2f}"
)

print(
    f"Momentum    : "
    f"{indicators['momentum']:.2f}%"
)


# ============================================================
# SIMULATED INDIA VIX
#
# We are deliberately using a controlled value here.
# Live VIX integration comes later.
# ============================================================

test_vix = 14.0


# ============================================================
# RUN REGIME ENGINE
# ============================================================

regime = regime_engine.detect_regime(
    adx=indicators["adx"],
    fast_ema=indicators["fast_ema"],
    slow_ema=indicators["slow_ema"],
    vix=test_vix,
    atr=indicators["atr"],
    spot_price=indicators["close"],
)


# ============================================================
# DISPLAY REGIME RESULT
# ============================================================

print("\n🤖 REGIME ENGINE")

print("-" * 70)

print(
    f"Regime      : "
    f"{regime['regime']}"
)

print(
    f"Confidence  : "
    f"{regime['confidence']}%"
)

print(
    f"ADX State   : "
    f"{regime['adx_state']}"
)

print(
    f"EMA State   : "
    f"{regime['ema_state']}"
)

print(
    f"VIX State   : "
    f"{regime['vix_state']}"
)

print(
    f"ATR State   : "
    f"{regime['atr_state']}"
)


print("\nReasons:")

for reason in regime["reasons"]:

    print(
        f"• {reason}"
    )


print("\n" + "=" * 70)

print(
    "🔒 TEST ONLY — NO ORDER PLACEMENT"
)

print("=" * 70)