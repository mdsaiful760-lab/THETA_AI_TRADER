import os
from dotenv import load_dotenv, set_key
from kiteconnect import KiteConnect

load_dotenv()

API_KEY = os.getenv("KITE_API_KEY")
API_SECRET = os.getenv("KITE_API_SECRET")

if not API_KEY or not API_SECRET:
    raise ValueError("KITE_API_KEY or KITE_API_SECRET missing from .env")

kite = KiteConnect(api_key=API_KEY)

print("=" * 60)
print("🚀 THETA AI TRADER — Kite Login")
print("=" * 60)

print("\nOpen this URL in your browser:")
print(kite.login_url())

print("\nAfter login, Zerodha will redirect you.")
print("Copy the request_token from the redirected URL.")

request_token = input("\nEnter request_token: ").strip()

try:
    data = kite.generate_session(
        request_token,
        api_secret=API_SECRET
    )

    access_token = data["access_token"]
    kite.set_access_token(access_token)

    # Save access token securely inside .env
    set_key(".env", "KITE_ACCESS_TOKEN", access_token)

    print("\n✅ KITE LOGIN SUCCESSFUL")
    print("User:", data.get("user_name"))
    print("✅ Access token saved securely in .env")

    # Verify connection
    profile = kite.profile()

    print("\n📊 ZERODHA PROFILE")
    print("User ID:", profile.get("user_id"))
    print("User Name:", profile.get("user_name"))
    print("Broker:", profile.get("broker"))

except Exception as e:
    print("\n❌ LOGIN FAILED")
    print("Error:", e)