import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from kiteconnect import KiteConnect
from regime_engine import RegimeEngine
from strategy_engine import StrategyEngine


# ============================================================
# EMA CALCULATION
# ============================================================

def calculate_ema(candles, period):

    if not candles:
        raise ValueError("No candles provided")

    if len(candles) < period:
        raise ValueError(
            f"Not enough candles for EMA {period}. "
            f"Received only {len(candles)} candles."
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

    if not candles:
        raise ValueError(
            "No candles provided for ATR calculation"
        )

    if len(candles) < period + 1:
        raise ValueError(
            f"Not enough candles for ATR {period}. "
            f"Received only {len(candles)} candles."
        )

    true_ranges = []

    for i in range(1, len(candles)):

        high = float(candles[i]["high"])
        low = float(candles[i]["low"])
        previous_close = float(
            candles[i - 1]["close"]
        )

        true_range = max(
            high - low,
            abs(high - previous_close),
            abs(low - previous_close),
        )

        true_ranges.append(true_range)

    atr = sum(true_ranges[:period]) / period

    for true_range in true_ranges[period:]:
        atr = (
            (atr * (period - 1))
            + true_range
        ) / period

    return atr


# ============================================================
# SESSION-AWARE ATR HISTORY
# ============================================================

def calculate_session_atr_history(
    candles,
    period=14,
):

    candles_by_date = {}

    for candle in candles:

        candle_date = candle["date"].date()

        candles_by_date.setdefault(
            candle_date,
            [],
        ).append(candle)

    atr_percentages = []

    for candle_date in sorted(candles_by_date):

        session_candles = (
            candles_by_date[candle_date]
        )

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

    if not candles:
        raise ValueError(
            "No candles provided for ADX calculation"
        )

    if len(candles) < (period * 2) + 1:
        raise ValueError(
            f"Not enough candles for ADX {period}. "
            f"Received only {len(candles)} candles."
        )

    true_ranges = []
    plus_dm_values = []
    minus_dm_values = []

    for i in range(1, len(candles)):

        current_high = float(
            candles[i]["high"]
        )
        current_low = float(
            candles[i]["low"]
        )

        previous_high = float(
            candles[i - 1]["high"]
        )
        previous_low = float(
            candles[i - 1]["low"]
        )
        previous_close = float(
            candles[i - 1]["close"]
        )

        true_range = max(
            current_high - current_low,
            abs(current_high - previous_close),
            abs(current_low - previous_close),
        )

        true_ranges.append(true_range)

        up_move = (
            current_high - previous_high
        )

        down_move = (
            previous_low - current_low
        )

        plus_dm = (
            up_move
            if (
                up_move > down_move
                and up_move > 0
            )
            else 0.0
        )

        minus_dm = (
            down_move
            if (
                down_move > up_move
                and down_move > 0
            )
            else 0.0
        )

        plus_dm_values.append(plus_dm)
        minus_dm_values.append(minus_dm)

    smoothed_tr = sum(
        true_ranges[:period]
    )

    smoothed_plus_dm = sum(
        plus_dm_values[:period]
    )

    smoothed_minus_dm = sum(
        minus_dm_values[:period]
    )

    dx_values = []

    for i in range(
        period,
        len(true_ranges),
    ):

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

        plus_di = (
            100
            * smoothed_plus_dm
            / smoothed_tr
        )

        minus_di = (
            100
            * smoothed_minus_dm
            / smoothed_tr
        )

        di_sum = plus_di + minus_di

        if di_sum == 0:
            dx = 0.0
        else:
            dx = (
                100
                * abs(plus_di - minus_di)
                / di_sum
            )

        dx_values.append(dx)

    if len(dx_values) < period:
        raise ValueError(
            "Not enough DX values to calculate ADX"
        )

    adx = (
        sum(dx_values[:period])
        / period
    )

    for dx in dx_values[period:]:
        adx = (
            (adx * (period - 1))
            + dx
        ) / period

    return adx


# ============================================================
# KITE CONNECTION
# ============================================================

load_dotenv()

api_key = os.getenv("KITE_API_KEY")
access_token = os.getenv("KITE_ACCESS_TOKEN")

if not api_key or not access_token:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing from .env"
    )

kite = KiteConnect(
    api_key=api_key
)

kite.set_access_token(
    access_token
)


# ============================================================
# FETCH NIFTY DATA
# ============================================================

NIFTY_TOKEN = 256265

candles = kite.historical_data(
    NIFTY_TOKEN,
    datetime.now() - timedelta(days=90),
    datetime.now(),
    "5minute",
)

if not candles:
    raise ValueError(
        "No NIFTY historical candles received"
    )


# ============================================================
# CALCULATE INDICATORS
# ============================================================

ema_9 = calculate_ema(candles, 9)
ema_21 = calculate_ema(candles, 21)

atr_14 = calculate_atr(
    candles,
    14,
)

adx_14 = calculate_adx(
    candles,
    14,
)

session_atr_history = (
    calculate_session_atr_history(
        candles,
        14,
    )
)

if not session_atr_history:
    raise ValueError(
        "Session ATR history is empty"
    )

session_atr_history_sorted = sorted(
    session_atr_history
)


# ============================================================
# INDIA VIX
# ============================================================

vix_data = kite.ltp(
    "NSE:INDIA VIX"
)

india_vix = float(
    vix_data[
        "NSE:INDIA VIX"
    ]["last_price"]
)


# ============================================================
# REGIME ENGINE
# ============================================================

regime_engine = RegimeEngine()

regime_result = regime_engine.detect_regime(
    adx=adx_14,
    fast_ema=ema_9,
    slow_ema=ema_21,
    vix=india_vix,
    atr=atr_14,
    spot_price=float(
        candles[-1]["close"]
    ),
    historical_atr_values=(
        session_atr_history_sorted
    ),
    candles=candles,
)


# ============================================================
# OUTPUT
# ============================================================

print("=" * 60)
print("🤖 THETA AI TRADER — MARKET REGIME ENGINE")
print("=" * 60)

print(
    f"NIFTY Spot       : "
    f"{float(candles[-1]['close']):.2f}"
)

print(
    f"Historical Data  : "
    f"{len(candles)} candles"
)

print(f"EMA 9            : {ema_9:.2f}")
print(f"EMA 21           : {ema_21:.2f}")
print(f"ATR 14           : {atr_14:.2f}")
print(f"ADX 14           : {adx_14:.2f}")
print(f"India VIX        : {india_vix:.2f}")

print("\n" + "=" * 60)
print("🧠 REGIME RESULT")
print("=" * 60)

print(
    f"Regime           : "
    f"{regime_result['regime']}"
)

print(
    f"Confidence       : "
    f"{regime_result['confidence']}%"
)

print(
    f"ADX State        : "
    f"{regime_result['adx_state']}"
)

print(
    f"EMA State        : "
    f"{regime_result['ema_state']}"
)

print(
    f"VIX State        : "
    f"{regime_result['vix_state']}"
)

print(
    f"ATR State        : "
    f"{regime_result['atr_state']}"
)

print("\n" + "=" * 60)
print("🛡️ SAFETY STATUS")
print("=" * 60)

print(
    f"Session Candles  : "
    f"{regime_result['session_candle_count']}"
)

print(
    f"Regime Ready     : "
    f"{regime_result['regime_ready']}"
)

print(
    f"Trade Permission : "
    f"{regime_result['trading_permission']}"
)

print("\n" + "=" * 60)
print("📋 ENGINE REASONS")
print("=" * 60)

for reason in regime_result["reasons"]:
    print(f"• {reason}")

print("=" * 60)


# ============================================================
# STRATEGY SELECTION ENGINE
# ============================================================

strategy_engine = StrategyEngine()

strategy_result = strategy_engine.select_strategy(
    regime_result
)

print("\n" + "=" * 60)
print("🎯 STRATEGY SELECTION ENGINE")
print("=" * 60)

print(
    f"Strategy          : "
    f"{strategy_result['strategy']}"
)

print(
    f"Action            : "
    f"{strategy_result['action']}"
)

print(
    f"Confidence        : "
    f"{strategy_result['confidence']}%"
)

print("\nStrategy Reasons:")

for reason in strategy_result["reasons"]:
    print(f"• {reason}")

print("=" * 60)
