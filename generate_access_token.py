from kiteconnect import KiteConnect
from dotenv import load_dotenv, set_key
import os

load_dotenv()

kite = KiteConnect(
    api_key=os.getenv("KITE_API_KEY")
)

request_token = input("Enter Request Token: ")

data = kite.generate_session(
    request_token=request_token,
    api_secret=os.getenv("KITE_API_SECRET")
)

access_token = data["access_token"]

print("\nACCESS TOKEN:")
print(access_token)

# Persist the new access token so main.py picks it up on next startup.
# set_key() rewrites KITE_ACCESS_TOKEN only (appending it if absent),
# preserves KITE_API_KEY/KITE_API_SECRET and every other line/comment
# untouched, and writes atomically (temp file + rename) internally.
set_key(".env", "KITE_ACCESS_TOKEN", access_token)

print("\n✓ Access token updated successfully in .env")