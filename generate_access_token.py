from kiteconnect import KiteConnect
from dotenv import load_dotenv
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

print("\nACCESS TOKEN:")
print(data["access_token"])