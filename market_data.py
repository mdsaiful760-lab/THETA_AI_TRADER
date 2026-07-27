import os
from dotenv import load_dotenv
from kiteconnect import KiteConnect

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
ACCESS_TOKEN = os.getenv("KITE_ACCESS_TOKEN")

if not API_KEY or not ACCESS_TOKEN:
    raise ValueError(
        "KITE_API_KEY or KITE_ACCESS_TOKEN missing. Run kite_login.py first."
    )

kite = KiteConnect(api_key=API_KEY)
kite.set_access_token(ACCESS_TOKEN)

print("=" * 60)
print("📊 THETA AI TRADER — MARKET DATA")
print("=" * 60)

try:
    # Fetch NIFTY 50 quote
    quote = kite.quote("NSE:NIFTY 50")
    nifty = quote["NSE:NIFTY 50"]

    ltp = nifty["last_price"]
    ohlc = nifty["ohlc"]

    previous_close = ohlc["close"]
    change = ltp - previous_close
    change_percent = (change / previous_close) * 100

    print("\n🇮🇳 NIFTY 50")
    print("-" * 40)

    print(f"LTP            : {ltp}")
    print(f"Open           : {ohlc['open']}")
    print(f"High           : {ohlc['high']}")
    print(f"Low            : {ohlc['low']}")
    print(f"Previous Close : {previous_close}")
    print(f"Change         : {change:.2f}")
    print(f"Change %       : {change_percent:.2f}%")

    print("\n✅ LIVE MARKET DATA CONNECTION SUCCESSFUL")

except Exception as e:
    print("\n❌ MARKET DATA ERROR")
    print("Error:", e)