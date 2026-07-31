import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from regime_engine import RegimeEngine


def calculate_ema(candles, period):

    if not candles:
        raise ValueError("No candles provided")

    if len(candles) < period:
        raise ValueError(
            f"Not enough candles for EMA {period}"
        )

    closes = [
        float(candle["close"])
        for candle in candles
    ]

    ema = sum(closes[:period]) / period
    multiplier = 2 / (period + 1)

    for close in closes[period:]:
        ema = (
            (close - ema) * multiplier
            + ema
        )

    return ema


# ============================================================
# ATR CALCULATION
# ============================================================

def calculate_atr(candles, period=14):
    """
    Calculate Average True Range (ATR).
    Returns the latest ATR value.
    """

    if not candles:
        raise ValueError("No candles provided for ATR calculation")

    if len(candles) < period + 1:
        raise ValueError(
            f"Not enough candles for ATR {period}. "
            f"Received only {len(candles)} candles."
        )

    true_ranges = []

    for i in range(1, len(candles)):

        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_close = float(candles[i - 1]["close"])

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(true_range)

    # Initial ATR = average of first 14 true ranges
    atr = sum(true_ranges[:period]) / period

    # Wilder's ATR smoothing
    for true_range in true_ranges[period:]:
        atr = (
            (atr * (period - 1)) + true_range
        ) / period

    return atr

# ============================================================
# HISTORICAL ATR PERCENTAGES
# ============================================================

def calculate_atr_history(candles, period=14):
    """
    Calculate rolling ATR percentage history.
    Used for volatility calibration.
    """

    atr_percentages = []

    if len(candles) < period + 1:
        return atr_percentages

    # Start after enough candles exist for ATR
    for i in range(period + 1, len(candles) + 1):

        candle_slice = candles[:i]

        atr = calculate_atr(
            candle_slice,
            period,
        )

        latest_close = float(
            candle_slice[-1]["close"]
        )

        if latest_close <= 0:
            continue

        atr_pct = (
            atr / latest_close
        ) * 100

        atr_percentages.append(
            atr_pct
        )

    return atr_percentages
# ============================================================
# SESSION-AWARE ATR HISTORY
# ============================================================

def calculate_session_atr_history(candles, period=14):
    """
    Calculate ATR percentage separately for each trading day.

    This prevents the previous day's close from being used
    as the previous close for the next trading day's opening
    candle.
    """

    candles_by_date = {}

    # Group candles by trading date
    for candle in candles:

        candle_date = candle["date"].date()

        if candle_date not in candles_by_date:
            candles_by_date[candle_date] = []

        candles_by_date[candle_date].append(candle)

    atr_percentages = []

    # Calculate ATR independently inside each trading session
    for candle_date in sorted(candles_by_date):

        session_candles = candles_by_date[candle_date]

        # Need enough candles to calculate ATR
        if len(session_candles) < period + 1:
            continue

        for i in range(
            period + 1,
            len(session_candles) + 1,
        ):

            candle_slice = session_candles[:i]

            atr = calculate_atr(
                candle_slice,
                period,
            )

            latest_close = float(
                candle_slice[-1]["close"]
            )

            if latest_close <= 0:
                continue

            atr_pct = (
                atr / latest_close
            ) * 100

            atr_percentages.append(
                atr_pct
            )

    return atr_percentages

# ============================================================
# ADX CALCULATION
# ============================================================

def calculate_adx(candles, period=14):
    """
    Calculate Average Directional Index (ADX)
    using Wilder's smoothing.
    Returns the latest ADX value.
    """

    if not candles:
        raise ValueError("No candles provided for ADX calculation")

    if len(candles) < (period * 2) + 1:
        raise ValueError(
            f"Not enough candles for ADX {period}. "
            f"Received only {len(candles)} candles."
        )

    true_ranges = []
    plus_dm_values = []
    minus_dm_values = []

    for i in range(1, len(candles)):

        current_high = float(candles[i]["high"])
        current_low = float(candles[i]["low"])

        previous_high = float(candles[i - 1]["high"])
        previous_low = float(candles[i - 1]["low"])
        previous_close = float(candles[i - 1]["close"])

        # True Range
        true_range = max(
            current_high - current_low,
            abs(current_high - previous_close),
            abs(current_low - previous_close),
        )

        true_ranges.append(true_range)

        # Directional Movement
        up_move = current_high - previous_high
        down_move = previous_low - current_low

        plus_dm = (
            up_move
            if up_move > down_move and up_move > 0
            else 0.0
        )

        minus_dm = (
            down_move
            if down_move > up_move and down_move > 0
            else 0.0
        )

        plus_dm_values.append(plus_dm)
        minus_dm_values.append(minus_dm)

    # Initial Wilder smoothed values
    smoothed_tr = sum(true_ranges[:period])
    smoothed_plus_dm = sum(plus_dm_values[:period])
    smoothed_minus_dm = sum(minus_dm_values[:period])

    dx_values = []

    for i in range(period, len(true_ranges)):

        smoothed_tr = (
            smoothed_tr
            - (smoothed_tr / period)
            + true_ranges[i]
        )

        smoothed_plus_dm = (
            smoothed_plus_dm
            - (smoothed_plus_dm / period)
            + plus_dm_values[i]
        )

        smoothed_minus_dm = (
            smoothed_minus_dm
            - (smoothed_minus_dm / period)
            + minus_dm_values[i]
        )

        if smoothed_tr == 0:
            continue

        plus_di = 100 * (
            smoothed_plus_dm / smoothed_tr
        )

        minus_di = 100 * (
            smoothed_minus_dm / smoothed_tr
        )

        di_sum = plus_di + minus_di

        if di_sum == 0:
            dx = 0.0
        else:
            dx = 100 * (
                abs(plus_di - minus_di) / di_sum
            )

        dx_values.append(dx)

    if len(dx_values) < period:
        raise ValueError(
            "Not enough DX values to calculate ADX"
        )

    # Initial ADX
    adx = sum(dx_values[:period]) / period

    # Wilder smoothing for remaining DX values
    for dx in dx_values[period:]:
        adx = (
            (adx * (period - 1)) + dx
        ) / period

    return adx

load_dotenv()

kite = KiteConnect(
    api_key=os.getenv("KITE_API_KEY")
)

kite.set_access_token(
    os.getenv("KITE_ACCESS_TOKEN")
)

candles = kite.historical_data(
    256265,
    datetime.now() - timedelta(days=90),
    datetime.now(),
    "5minute",
)

ema_9 = calculate_ema(candles, 9)
ema_21 = calculate_ema(candles, 21)
atr_14 = calculate_atr(candles, 14)
adx_14 = calculate_adx(candles, 14)
atr_history = calculate_atr_history(
    candles,
    14,
)

atr_history_sorted = sorted(
    atr_history
)
session_atr_history = calculate_session_atr_history(
    candles,
    14,
)

session_atr_history_sorted = sorted(
    session_atr_history
)

# ============================================================
# CURRENT ATR PERCENTAGE
# ============================================================

current_close = float(
    candles[-1]["close"]
)

current_atr_pct = (
    atr_14 / current_close
) * 100

def percentile(values, pct):
    """
    Return percentile value from a sorted list.
    """

    if not values:
        raise ValueError(
            "No values provided for percentile calculation"
        )

    index = (len(values) - 1) * pct

    lower = int(index)
    upper = min(
        lower + 1,
        len(values) - 1,
    )

    weight = index - lower

    return (
        values[lower] * (1 - weight)
        + values[upper] * weight
    )
# ============================================================
# ADAPTIVE ATR CLASSIFICATION
# ============================================================

def classify_adaptive_atr(
    current_atr_pct,
    historical_atr_values,
):
    """
    Classify current 5-minute ATR relative to the
    historical session-aware ATR distribution.
    """

    if not historical_atr_values:
        raise ValueError(
            "Historical ATR values are required"
        )

    values = sorted(
        historical_atr_values
    )

    p25 = percentile(values, 0.25)
    p75 = percentile(values, 0.75)
    p95 = percentile(values, 0.95)

    if current_atr_pct <= p25:
        return "LOW_ATR"

    elif current_atr_pct <= p75:
        return "NORMAL_ATR"

    elif current_atr_pct <= p95:
        return "HIGH_ATR"

    else:
        return "EXTREME_ATR"

# ============================================================
# INTRADAY REGIME WARM-UP CHECK
# ============================================================

def check_regime_warmup(candles, required_candles=15):
    """
    Check whether enough candles exist in the current
    trading session for reliable intraday regime analysis.
    """

    if not candles:
        return False, 0

    latest_date = candles[-1]["date"].date()

    today_candles = [
        candle
        for candle in candles
        if candle["date"].date() == latest_date
    ]

    candle_count = len(today_candles)

    regime_ready = (
        candle_count >= required_candles
    )

    return regime_ready, candle_count

adaptive_atr_state = classify_adaptive_atr(
    current_atr_pct,
    session_atr_history_sorted,
)


regime_ready, session_candle_count = check_regime_warmup(
    candles,
    required_candles=15,
)


# ============================================================
# WARM-UP SAFETY TEST
# ============================================================

latest_date = candles[-1]["date"].date()

latest_session_candles = [
    candle
    for candle in candles
    if candle["date"].date() == latest_date
]

# Simulate an early-morning session with only 10 candles
test_morning_candles = latest_session_candles[:10]

test_regime_ready, test_candle_count = check_regime_warmup(
    test_morning_candles,
    required_candles=15,
)

vix_data = kite.ltp("NSE:INDIA VIX")
india_vix = float(
    vix_data["NSE:INDIA VIX"]["last_price"]
)
regime_engine = RegimeEngine()

engine_regime_ready, engine_session_candle_count = (
    regime_engine.check_warmup(
        candles,
        required_candles=15,
    )
)

engine_test_regime_ready, engine_test_candle_count = (
    regime_engine.check_warmup(
        test_morning_candles,
        required_candles=15,
    )
)

engine_adaptive_atr_state = regime_engine.analyze_adaptive_atr(
    atr=atr_14,
    spot_price=current_close,
    historical_atr_values=session_atr_history_sorted,
)

regime_result = regime_engine.detect_regime(
    adx=adx_14,
    fast_ema=ema_9,
    slow_ema=ema_21,
    vix=india_vix,
    atr=atr_14,
    spot_price=float(candles[-1]["close"]),
    historical_atr_values=session_atr_history_sorted,
    candles=candles,
)

print("=" * 50)
print("🤖 THETA AI TRADER — REGIME INDICATOR TEST")
print("=" * 50)

print(f"Candles : {len(candles)}")
print(f"EMA 9   : {ema_9:.2f}")
print(f"EMA 21  : {ema_21:.2f}")
print(f"ATR 14  : {atr_14:.2f}")
print(f"ADX 14  : {adx_14:.2f}")
print(f"India VIX: {india_vix:.2f}")
print("\n" + "=" * 50)
print("📊 ATR VOLATILITY DISTRIBUTION")
print("=" * 50)

print(
    f"Samples : {len(atr_history_sorted)}"
)

print(
    f"Minimum : {min(atr_history_sorted):.4f}%"
)

print(
    f"25th    : {percentile(atr_history_sorted, 0.25):.4f}%"
)

print(
    f"Median  : {percentile(atr_history_sorted, 0.50):.4f}%"
)

print(
    f"75th    : {percentile(atr_history_sorted, 0.75):.4f}%"
)

print(
    f"90th    : {percentile(atr_history_sorted, 0.90):.4f}%"
)

print(
    f"95th    : {percentile(atr_history_sorted, 0.95):.4f}%"
)

print(
    f"Maximum : {max(atr_history_sorted):.4f}%"
)
print("\n" + "=" * 50)
print("📊 SESSION-AWARE ATR DISTRIBUTION")
print("=" * 50)

print(
    f"Samples : {len(session_atr_history_sorted)}"
)

print(
    f"Minimum : {min(session_atr_history_sorted):.4f}%"
)

print(
    f"25th    : {percentile(session_atr_history_sorted, 0.25):.4f}%"
)

print(
    f"Median  : {percentile(session_atr_history_sorted, 0.50):.4f}%"
)

print(
    f"75th    : {percentile(session_atr_history_sorted, 0.75):.4f}%"
)

print(
    f"90th    : {percentile(session_atr_history_sorted, 0.90):.4f}%"
)

print(
    f"95th    : {percentile(session_atr_history_sorted, 0.95):.4f}%"
)

print(
    f"Maximum : {max(session_atr_history_sorted):.4f}%"
)

print("\n" + "=" * 50)
print("🧠 REGIME ENGINE RESULT")
print("=" * 50)

print(f"Regime     : {regime_result['regime']}")
print(f"Confidence : {regime_result['confidence']}%")
print(f"ADX State  : {regime_result['adx_state']}")
print(f"EMA State  : {regime_result['ema_state']}")
print(f"VIX State  : {regime_result['vix_state']}")
print(f"ATR State  : {regime_result['atr_state']}")

print(f"Internal Ready      : {regime_result['regime_ready']}")
print(f"Internal Candles    : {regime_result['session_candle_count']}")
print(f"Internal Permission : {regime_result['trading_permission']}")
print("\nRegime Reasons:")

for reason in regime_result["reasons"]:
    print(f" - {reason}")

print(f"Current ATR %      : {current_atr_pct:.4f}%")
print(f"Old ATR State      : {regime_result['atr_state']}")
print(f"Adaptive ATR State : {adaptive_atr_state}")
print(f"Engine Adaptive ATR: {engine_adaptive_atr_state}")

print(f"Session Candles     : {session_candle_count}")
print(f"Engine Session Count: {engine_session_candle_count}")
print(f"Engine Regime Ready : {engine_regime_ready}")

if regime_ready:
    print("Regime Status       : READY")
    print("Trading Permission  : ALLOWED")
else:
    print("Regime Status       : WARMING UP")
    print("Trading Permission  : NO NEW TRADE")
    print("\n" + "=" * 50)
print("🛡️ MORNING WARM-UP SAFETY TEST")
print("=" * 50)

print(f"Simulated Candles   : {test_candle_count}")
print(f"Engine Test Candles : {engine_test_candle_count}")
print(f"Engine Test Ready   : {engine_test_regime_ready}")

if test_regime_ready:
    print("Regime Status       : READY")
    print("Trading Permission  : ALLOWED")
else:
    print("Regime Status       : WARMING UP")
    print("Trading Permission  : NO NEW TRADE")

if ema_9 > ema_21:
    print("Trend   : BULLISH")
elif ema_9 < ema_21:
    print("Trend   : BEARISH")
else:
    print("Trend   : NEUTRAL")