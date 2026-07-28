import os
import math
from datetime import date, datetime, timedelta
from dotenv import load_dotenv
from kiteconnect import KiteConnect

# Import your Greeks engine
from greeks import implied_volatility, calculate_greeks
# Import risk management engine
from risk_manager import RiskManager
from order_manager import PaperOrderManager
from regime_engine import RegimeEngine

# ============================================================
# CONFIGURATION
# ============================================================

TARGET_DELTA = 0.15
MIN_DELTA = 0.10
MAX_DELTA = 0.20

STRIKES_EACH_SIDE = 15

RISK_FREE_RATE = 0.06

MIN_COMBINED_PREMIUM = 20.0

PCR_BULLISH_LIMIT = 1.30
PCR_BEARISH_LIMIT = 0.70
# ============================================================
# TRADE SAFETY GATE
# ============================================================

MAX_ALLOWED_LOTS = 10
MIN_TRADE_SCORE = 7
MIN_TRADE_CONFIDENCE = 7

ENABLE_ORDER_PLACEMENT = False
# ============================================================
# RISK MANAGEMENT
# ============================================================

risk_manager = RiskManager(
    capital=2_000_000,
    max_risk_per_trade_pct=1,
    max_daily_loss_pct=2,
)
paper_order_manager = PaperOrderManager()

regime_engine = RegimeEngine()

# ============================================================
# KITE CONNECTION
# ============================================================

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

if not API_KEY or not ACCESS_TOKEN:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing from .env"
    )

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

# ============================================================
# NIFTY HISTORICAL CANDLES
# ============================================================

def get_nifty_historical_candles(
    instrument_token,
    days=10,
    interval="5minute",
):
    """
    Fetch historical NIFTY candles from Kite.
    """

    to_date = datetime.now()
    from_date = to_date - timedelta(days=days)

    candles = kite.historical_data(
        instrument_token=instrument_token,
        from_date=from_date,
        to_date=to_date,
        interval=interval,
    )

    return candles


# ============================================================
# EMA CALCULATION
# ============================================================

def calculate_ema(candles, period):
    """
    Calculate EMA using candle closing prices.
    Returns the latest EMA value.
    """

    if not candles:
        raise ValueError("No candles provided for EMA calculation")

    if len(candles) < period:
        raise ValueError(
            f"Not enough candles for EMA {period}. "
            f"Received only {len(candles)} candles."
        )

    closes = [
        float(candle["close"])
        for candle in candles
    ]

    # Start EMA with SMA of the first 'period' closes
    ema = sum(closes[:period]) / period

    # Standard EMA smoothing multiplier
    multiplier = 2 / (period + 1)

    # Calculate EMA for remaining candles
    for close in closes[period:]:
        ema = (
            (close - ema) * multiplier
            + ema
        )

    return ema


print("=" * 72)
print("🤖 THETA AI TRADER — DECISION ENGINE")
print("=" * 72)


# ============================================================
# GET NIFTY SPOT
# ============================================================

# Load NSE instruments to find NIFTY 50 token
nse_instruments = kite.instruments("NSE")

nifty_instrument = next(
    (
        instrument
        for instrument in nse_instruments
        if instrument["tradingsymbol"] == "NIFTY 50"
    ),
    None,
)

if nifty_instrument is None:
    raise RuntimeError(
        "NIFTY 50 instrument token not found."
    )

nifty_token = nifty_instrument["instrument_token"]

print(
    f"🔑 NIFTY TOKEN: {nifty_token}"
)


spot_data = kite.ltp("NSE:NIFTY 50")
spot = float(
    spot_data["NSE:NIFTY 50"]["last_price"]
)

print(f"\n📊 NIFTY SPOT : {spot:.2f}")


# ============================================================
# LOAD NIFTY OPTION INSTRUMENTS
# ============================================================

print("📥 Loading NIFTY instruments...")

instruments = kite.instruments("NFO")

nifty_options = [
    i for i in instruments
    if i["name"] == "NIFTY"
    and i["instrument_type"] in ("CE", "PE")
]

if not nifty_options:
    raise RuntimeError("No NIFTY options found.")


# ============================================================
# FIND NEAREST EXPIRY
# ============================================================

today = date.today()

expiries = sorted({
    i["expiry"]
    for i in nifty_options
    if i["expiry"] >= today
})

if not expiries:
    raise RuntimeError("No future NIFTY expiry found.")

expiry = expiries[0]

print(f"📅 EXPIRY     : {expiry}")


expiry_options = [
    i for i in nifty_options
    if i["expiry"] == expiry
]


# ============================================================
# STRIKES
# ============================================================

strikes = sorted({
    float(i["strike"])
    for i in expiry_options
})

if len(strikes) < 2:
    raise RuntimeError("Not enough strikes available.")

steps = [
    strikes[i + 1] - strikes[i]
    for i in range(len(strikes) - 1)
    if strikes[i + 1] > strikes[i]
]

strike_step = min(steps)

atm = min(
    strikes,
    key=lambda x: abs(x - spot)
)

print(f"🎯 ATM STRIKE : {atm:.0f}")
print(f"📏 STRIKE STEP: {strike_step:.0f}")


nearby_strikes = [
    strike
    for strike in strikes
    if atm - STRIKES_EACH_SIDE * strike_step
    <= strike
    <= atm + STRIKES_EACH_SIDE * strike_step
]


# ============================================================
# OPTION MAP
# ============================================================

option_map = {}

for instrument in expiry_options:

    strike = float(instrument["strike"])

    if strike not in nearby_strikes:
        continue

    option_map.setdefault(strike, {})

    option_map[strike][
        instrument["instrument_type"]
    ] = instrument


# ============================================================
# FETCH LIVE QUOTES
# ============================================================

symbols = []

for strike in nearby_strikes:

    legs = option_map.get(strike, {})

    for option_type in ("CE", "PE"):

        instrument = legs.get(option_type)

        if instrument:

            symbols.append(
                f'NFO:{instrument["tradingsymbol"]}'
            )


print("📡 Fetching live option data...")

quotes = kite.quote(symbols)


# ============================================================
# TIME TO EXPIRY
# ============================================================

now = datetime.now()

expiry_datetime = datetime.combine(
    expiry,
    datetime.strptime(
        "15:30",
        "%H:%M"
    ).time()
)

seconds_remaining = (
    expiry_datetime - now
).total_seconds()

# Prevent zero/negative T
time_to_expiry = max(
    seconds_remaining /
    (365 * 24 * 60 * 60),
    1 / (365 * 24)
)


# ============================================================
# ANALYZE OPTIONS
# ============================================================

candidates_ce = []
candidates_pe = []

total_ce_oi = 0
total_pe_oi = 0

ce_oi_levels = []
pe_oi_levels = []


print("🧮 Calculating IV, Greeks and OI structure...")


for strike in nearby_strikes:

    legs = option_map.get(strike, {})

    for option_type in ("CE", "PE"):

        instrument = legs.get(option_type)

        if not instrument:
            continue

        key = f'NFO:{instrument["tradingsymbol"]}'

        q = quotes.get(key, {})

        premium = float(
            q.get("last_price", 0) or 0
        )

        oi = int(
            q.get("oi", 0) or 0
        )

        if option_type == "CE":

            total_ce_oi += oi

            ce_oi_levels.append(
                (strike, oi)
            )

        else:

            total_pe_oi += oi

            pe_oi_levels.append(
                (strike, oi)
            )


        if premium <= 0:
            continue


        try:

            iv = implied_volatility(
                market_price=premium,
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                rate=RISK_FREE_RATE,
                option_type=option_type
            )

            if iv is None:
                continue

            greeks = calculate_greeks(
                spot=spot,
                strike=strike,
                time_to_expiry=time_to_expiry,
                rate=RISK_FREE_RATE,
                volatility=iv,
                option_type=option_type
            )

            delta = float(
                greeks["delta"]
            )

        except Exception as e:
            print(
                f"⚠️ Greek calculation failed: "
                f"{instrument['tradingsymbol']} | "
                f"Strike {strike:.0f} | "
                f"{option_type} | Error: {e}"
            )
            continue


        data = {
            "symbol": instrument["tradingsymbol"],
            "strike": strike,
            "premium": premium,
            "oi": oi,
            "iv": iv,
            "delta": delta,
            "theta": float(
                greeks.get("theta", 0)
            ),
            "gamma": float(
                greeks.get("gamma", 0)
            ),
            "vega": float(
                greeks.get("vega", 0)
            )
        }


        abs_delta = abs(delta)

        if MIN_DELTA <= abs_delta <= MAX_DELTA:

            if option_type == "CE":
                candidates_ce.append(data)

            else:
                candidates_pe.append(data)


# ============================================================
# FIND TARGET DELTA OPTIONS
# ============================================================

def closest_delta(options):

    if not options:
        return None

    return min(
        options,
        key=lambda x:
        abs(
            abs(x["delta"])
            - TARGET_DELTA
        )
    )


ce = closest_delta(candidates_ce)
pe = closest_delta(candidates_pe)


# ============================================================
# MARKET STRUCTURE
# ============================================================

pcr = (
    total_pe_oi / total_ce_oi
    if total_ce_oi > 0
    else 0
)


valid_ce_oi = [
    x for x in ce_oi_levels
    if x[1] > 0
]

valid_pe_oi = [
    x for x in pe_oi_levels
    if x[1] > 0
]


resistance = (
    max(
        valid_ce_oi,
        key=lambda x: x[1]
    )[0]
    if valid_ce_oi
    else None
)


support = (
    max(
        valid_pe_oi,
        key=lambda x: x[1]
    )[0]
    if valid_pe_oi
    else None
)


# ============================================================
# DISPLAY MARKET STRUCTURE
# ============================================================

print("\n" + "=" * 72)
print("📊 MARKET STRUCTURE")
print("=" * 72)

print(f"\nPCR            : {pcr:.2f}")

print(
    f"Support        : "
    f"{support:.0f}"
    if support is not None
    else "Support        : N/A"
)

print(
    f"Resistance     : "
    f"{resistance:.0f}"
    if resistance is not None
    else "Resistance     : N/A"
)


# ============================================================
# VERIFY CANDIDATES
# ============================================================

if not ce or not pe:

    print("\n" + "=" * 72)

    print("🟡 FINAL SIGNAL : WAIT")

    print(
        "\nReason: Suitable CE/PE options "
        "were not found inside the "
        "configured delta range."
    )

    print("\n🔒 READ-ONLY — NO ORDER PLACEMENT")

    raise SystemExit


# ============================================================
# STRANGLE METRICS
# ============================================================

combined_premium = (
    ce["premium"] +
    pe["premium"]
)

upper_be = (
    ce["strike"] +
    combined_premium
)

lower_be = (
    pe["strike"] -
    combined_premium
)
# ============================================================
# RISK CALCULATION
# ============================================================

strangle_risk = risk_manager.calculate_strangle_risk(
    ce_premium=ce["premium"],
    pe_premium=pe["premium"],
    lot_size=65,
    stop_loss_pct=30,
)

recommended_lots = risk_manager.calculate_lots(
    risk_per_lot=strangle_risk["risk_per_lot"],
    max_lots=MAX_ALLOWED_LOTS,
)

# Short position Greeks
net_delta = -(
    ce["delta"] +
    pe["delta"]
)

net_theta = -(
    ce["theta"] +
    pe["theta"]
)

net_vega = -(
    ce["vega"] +
    pe["vega"]
)

net_gamma = -(
    ce["gamma"] +
    pe["gamma"]
)


delta_difference = abs(
    abs(ce["delta"])
    -
    abs(pe["delta"])
)


# ============================================================
# DISPLAY PROPOSED STRANGLE
# ============================================================

print("\n" + "=" * 72)
print("🎯 PROPOSED SHORT STRANGLE")
print("=" * 72)


print("\n🔴 CE SELL CANDIDATE")

print(f"Symbol   : {ce['symbol']}")
print(f"Strike   : {ce['strike']:.0f}")
print(f"Delta    : {ce['delta']:.3f}")
print(f"Premium  : {ce['premium']:.2f}")
print(f"IV       : {ce['iv'] * 100:.2f}%")
print(f"OI       : {ce['oi']:,}")


print("\n🟢 PE SELL CANDIDATE")

print(f"Symbol   : {pe['symbol']}")
print(f"Strike   : {pe['strike']:.0f}")
print(f"Delta    : {pe['delta']:.3f}")
print(f"Premium  : {pe['premium']:.2f}")
print(f"IV       : {pe['iv'] * 100:.2f}%")
print(f"OI       : {pe['oi']:,}")


print("\nSTRANGLE METRICS")
print("----------------")

print(
    f"Combined Premium : "
    f"{combined_premium:.2f}"
)

print(
    f"Lower B/E        : "
    f"{lower_be:.2f}"
)

print(
    f"Upper B/E        : "
    f"{upper_be:.2f}"
)


print("\nPOSITION GREEKS")
print("----------------")

print(f"Net Delta : {net_delta:.4f}")
print(f"Net Theta : {net_theta:.2f}")
print(f"Net Vega  : {net_vega:.2f}")
print(f"Net Gamma : {net_gamma:.6f}")


# ============================================================
# DECISION ENGINE
# ============================================================

score = 0
reasons = []
warnings = []


# ------------------------------------------------------------
# DELTA BALANCE
# ------------------------------------------------------------

if delta_difference <= 0.03:

    score += 2

    reasons.append(
        "CE/PE delta balance is good"
    )

elif delta_difference <= 0.05:

    score += 1

    reasons.append(
        "CE/PE delta balance is acceptable"
    )

else:

    warnings.append(
        "CE/PE deltas are unbalanced"
    )


# ------------------------------------------------------------
# PCR
# ------------------------------------------------------------

if PCR_BEARISH_LIMIT <= pcr <= PCR_BULLISH_LIMIT:

    score += 2

    reasons.append(
        "PCR indicates a relatively balanced market"
    )

else:

    warnings.append(
        "PCR indicates directional OI imbalance"
    )


# ------------------------------------------------------------
# SUPPORT / RESISTANCE
# ------------------------------------------------------------

inside_range = False

if support is not None and resistance is not None:

    low_level = min(
        support,
        resistance
    )

    high_level = max(
        support,
        resistance
    )

    # Allow 50-point tolerance around major OI levels
    OI_RANGE_TOLERANCE = 50

    if (low_level - OI_RANGE_TOLERANCE) <= spot <= (
        high_level + OI_RANGE_TOLERANCE
    ):
        inside_range = True
        score += 2

        reasons.append(
            "Spot is trading inside/near major OI levels"
        )

    else:
        warnings.append(
            "Spot is outside major OI range"
        )


# ------------------------------------------------------------
# PREMIUM
# ------------------------------------------------------------

if combined_premium >= MIN_COMBINED_PREMIUM:

    score += 1

    reasons.append(
        "Combined premium passes minimum filter"
    )

else:

    warnings.append(
        "Combined premium is low"
    )


# ------------------------------------------------------------
# IV BALANCE
# ------------------------------------------------------------

iv_difference = abs(
    ce["iv"] -
    pe["iv"]
)

if iv_difference <= 0.05:

    score += 1

    reasons.append(
        "CE/PE implied volatility is reasonably balanced"
    )

else:

    warnings.append(
        "Large CE/PE IV difference detected"
    )


# ------------------------------------------------------------
# OI
# ------------------------------------------------------------

if ce["oi"] > 0 and pe["oi"] > 0:

    score += 1

    reasons.append(
        "Both selected strikes have visible open interest"
    )


# ============================================================
# HARD RISK FILTERS
# ============================================================

hard_reject = False


if abs(net_delta) > 0.08:

    hard_reject = True

    warnings.append(
        "Net position delta is too directional"
    )


if combined_premium <= 0:

    hard_reject = True

    warnings.append(
        "Invalid combined premium"
    )


if ce["strike"] <= spot:

    hard_reject = True

    warnings.append(
        "Selected CE is not OTM"
    )


if pe["strike"] >= spot:

    hard_reject = True

    warnings.append(
        "Selected PE is not OTM"
    )


# ============================================================
# FINAL SIGNAL
# ============================================================

if hard_reject:

    signal = "🔴 AVOID"

elif score >= 8:

    signal = "🟢 TRADE"

elif score >= 5:

    signal = "🟡 WAIT / CONDITIONAL"

else:

    signal = "🔴 AVOID"


confidence = min(
    10,
    round(score, 1)
)
# ============================================================
# TRADE SAFETY GATE
# ============================================================

safety_checks = {
    "Signal": signal == "🟢 TRADE",
    "Score": score >= MIN_TRADE_SCORE,
    "Confidence": confidence >= MIN_TRADE_CONFIDENCE,
    "Lots": 1 <= recommended_lots <= MAX_ALLOWED_LOTS,
}

trade_allowed = all(safety_checks.values())

# ============================================================
# FINAL REPORT
# ============================================================

print("\n" + "=" * 72)
print("🤖 THETA AI TRADER — FINAL DECISION")
print("=" * 72)

print(f"\nSignal     : {signal}")
print(f"Score      : {score}/9")
print(f"Confidence : {confidence}/10")
print()
print("🛡️ RISK MANAGEMENT")
print("-" * 40)

print(
    f"CE Stop Loss       : ₹{strangle_risk['ce_stop_loss']:.2f}"
)

print(
    f"PE Stop Loss       : ₹{strangle_risk['pe_stop_loss']:.2f}"
)

print(
    f"Risk / Lot         : ₹{strangle_risk['risk_per_lot']:,.2f}"
)

print(
    f"Max Risk / Trade   : ₹{risk_manager.max_trade_risk():,.0f}"
)

print(
    f"Recommended Lots   : {recommended_lots}"
)
print()
print("🛡️ TRADE SAFETY CHECK")
print("-" * 40)

for check_name, passed in safety_checks.items():
    status = "✅ PASS" if passed else "❌ FAIL"
    print(f"{check_name:<18}: {status}")

print()

if trade_allowed:
    print("FINAL STATUS       : 🟢 TRADE ALLOWED")
else:
    print("FINAL STATUS       : 🔴 TRADE BLOCKED")
if reasons:

    print("\n✅ POSITIVE FACTORS")

    for reason in reasons:
        print(f"• {reason}")


if warnings:

    print("\n⚠️ RISK FACTORS")

    for warning in warnings:
        print(f"• {warning}")

# ============================================================
# PAPER TRADE EXECUTION
# ============================================================

if trade_allowed:

    print("\n" + "=" * 72)
    print("📝 PAPER TRADE EXECUTION")
    print("=" * 72)

    ce_paper_position = paper_order_manager.sell_option(
        symbol=ce["symbol"],
        premium=ce["premium"],
        lots=recommended_lots,
        lot_size=65,
        stop_loss_price=strangle_risk["ce_stop_loss"],
    )

    pe_paper_position = paper_order_manager.sell_option(
        symbol=pe["symbol"],
        premium=pe["premium"],
        lots=recommended_lots,
        lot_size=65,
        stop_loss_price=strangle_risk["pe_stop_loss"],
    )

    print("\n✅ PAPER STRANGLE CREATED")
    print(f"Lots          : {recommended_lots}")
    print(f"Total Qty     : {recommended_lots * 65} per leg")

else:

    print("\n⛔ PAPER TRADE NOT CREATED")
    print("Reason: Trade safety checks did not pass")
print("\n" + "=" * 72)

print("🔒 SIGNAL ONLY — NO ORDER PLACEMENT")
print("❌ Kite place_order() is NOT used")

print("=" * 72)