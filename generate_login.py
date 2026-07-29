from kiteconnect import KiteConnect
from dotenv import load_dotenv
import os

load_dotenv()

kite = KiteConnect(
    api_key=os.getenv("KITE_API_KEY")
)

print(kite.login_url())