# ============================================================
# THETA AI TRADER
# REAL MARKET → INDICATOR → REGIME INTEGRATION TEST
# ============================================================

from market_data_engine import MarketDataEngine
from indicator_engine import IndicatorEngine
from regime_engine import RegimeEngine
from market_data_safety import MarketDataSafety


print("=" * 75)
print("🚀 THETA AI TRADER — REAL MARKET REGIME TEST")
print("=" * 75)


# ============================================================
# CREATE ENGINES
# ============================================================

market_data_engine = MarketDataEngine()

indicator_engine = IndicatorEngine()

regime_engine = RegimeEngine()

market_data_safety = MarketDataSafety()


# ============================================================
# FETCH REAL NIFTY MARKET DATA
# ============================================================

print("\n📡 Fetching real NIFTY 5-minute candles...")

candles = market_data_engine.get_nifty_candles(
    interval="5minute",
    lookback_days=5,
)

if not candles:
    raise RuntimeError(
        "No NIFTY candles available"
    )


# ============================================================
# MARKET DATA SAFETY CHECK
# ============================================================

latest_candle_timestamp = candles[-1]["date"]

data_safety = (
    market_data_safety.validate_candle_freshness(
        latest_candle_timestamp
    )
)

print(
    f"🛡️ Market Data Safety : "
    f"{data_safety['status']}"
)

print(
    f"🕒 Candle Age         : "
    f"{data_safety['age_minutes']:.2f} minutes"
)

print(
    f"📋 Safety Reason      : "
    f"{data_safety['reason']}"
)


if not data_safety["safe"]:

    raise RuntimeError(
        "Market data safety check failed: "
        f"{data_safety['status']} — "
        f"{data_safety['reason']}"
    )

# ============================================================
# RUN INDICATOR ENGINE
# ============================================================

indicators = indicator_engine.analyze(
    candles
)


# ============================================================
# FETCH REAL INDIA VIX
# ============================================================

print("📡 Fetching India VIX...")

india_vix = (
    market_data_engine.get_india_vix()
)


# ============================================================
# RUN REGIME ENGINE
# ============================================================

regime = regime_engine.detect_regime(
    adx=indicators["adx"],
    fast_ema=indicators["fast_ema"],
    slow_ema=indicators["slow_ema"],
    vix=india_vix,
    atr=indicators["atr"],
    spot_price=indicators["close"],
)


# ============================================================
# DISPLAY MARKET DATA
# ============================================================

print("\n" + "=" * 75)
print("📊 REAL NIFTY MARKET DATA")
print("=" * 75)

print(
    f"Latest Candle : "
    f"{candles[-1]['date']}"
)

print(
    f"Candles       : "
    f"{len(candles)}"
)

print(
    f"NIFTY Close   : "
    f"{indicators['close']:.2f}"
)

print(
    f"India VIX     : "
    f"{india_vix:.2f}"
)


# ============================================================
# DISPLAY INDICATORS
# ============================================================

print("\n" + "=" * 75)
print("🧮 TECHNICAL INDICATORS")
print("=" * 75)

print(
    f"EMA 5         : "
    f"{indicators['fast_ema']:.2f}"
)

print(
    f"EMA 21        : "
    f"{indicators['slow_ema']:.2f}"
)

print(
    f"EMA Structure : "
    f"{indicators['ema_structure']}"
)

print(
    f"RSI 14        : "
    f"{indicators['rsi']:.2f}"
)

print(
    f"ATR 14        : "
    f"{indicators['atr']:.2f}"
)

print(
    f"ADX 14        : "
    f"{indicators['adx']:.2f}"
)

print(
    f"Momentum      : "
    f"{indicators['momentum']:+.2f}%"
)


# ============================================================
# DISPLAY REGIME
# ============================================================

print("\n" + "=" * 75)
print("🤖 THETA AI TRADER — MARKET REGIME")
print("=" * 75)

print(
    f"Regime        : "
    f"{regime['regime']}"
)

print(
    f"Confidence    : "
    f"{regime['confidence']}%"
)

print(
    f"ADX State     : "
    f"{regime['adx_state']}"
)

print(
    f"EMA State     : "
    f"{regime['ema_state']}"
)

print(
    f"VIX State     : "
    f"{regime['vix_state']}"
)

print(
    f"ATR State     : "
    f"{regime['atr_state']}"
)

print(
    f"Data Ready    : "
    f"{regime['trading_permission']}"
)


print("\nReasons:")

for reason in regime["reasons"]:

    print(
        f"• {reason}"
    )


# ============================================================
# SAFETY
# ============================================================

print("\n" + "=" * 75)
print("🔒 ANALYSIS ONLY — NO ORDER PLACEMENT")
print("=" * 75)