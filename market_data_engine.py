# ============================================================
# THETA AI TRADER — MARKET DATA ENGINE
# ============================================================

import os
from datetime import datetime, timedelta

from dotenv import load_dotenv
from kiteconnect import KiteConnect


class MarketDataEngine:
    """
    Central market-data interface for THETA AI TRADER.

    Responsibilities:
    - Connect to broker market-data API
    - Fetch index prices
    - Fetch historical candles
    - Fetch volatility data
    - Normalize data for analytical engines

    This engine does NOT:
    - Generate trading signals
    - Select strategies
    - Place orders
    """

    def __init__(
        self,
        api_key=None,
        access_token=None,
    ):
        load_dotenv()

        self.api_key = (
            api_key
            or os.getenv("KITE_API_KEY")
        )

        self.access_token = (
            access_token
            or os.getenv("KITE_ACCESS_TOKEN")
        )

        if not self.api_key:
            raise ValueError(
                "KITE_API_KEY missing"
            )

        if not self.access_token:
            raise ValueError(
                "KITE_ACCESS_TOKEN missing"
            )

        self.kite = KiteConnect(
            api_key=self.api_key
        )

        self.kite.set_access_token(
            self.access_token
        )

    # --------------------------------------------------------
    # CONNECTION TEST
    # --------------------------------------------------------

    def test_connection(self):
        """
        Verify that the current Kite credentials are valid.

        Uses NIFTY 50 LTP as a read-only connection test.
        """

        data = self.kite.ltp(
            "NSE:NIFTY 50"
        )

        if "NSE:NIFTY 50" not in data:
            raise RuntimeError(
                "NIFTY 50 data not returned by Kite"
            )

        last_price = float(
            data["NSE:NIFTY 50"]["last_price"]
        )

        if last_price <= 0:
            raise RuntimeError(
                "Invalid NIFTY 50 price returned by Kite"
            )

        return {
            "connected": True,
            "instrument": "NIFTY 50",
            "last_price": last_price,
        }


    # --------------------------------------------------------
    # NIFTY INSTRUMENT TOKEN
    # --------------------------------------------------------

    def get_nifty_token(self):
        """
        Find the instrument token for NIFTY 50.
        """

        instruments = self.kite.instruments(
            "NSE"
        )

        for instrument in instruments:

            if (
                instrument.get("tradingsymbol")
                == "NIFTY 50"
            ):
                return int(
                    instrument["instrument_token"]
                )

        raise RuntimeError(
            "NIFTY 50 instrument token not found"
        )

    # --------------------------------------------------------
    # HISTORICAL NIFTY CANDLES
    # --------------------------------------------------------

    def get_nifty_candles(
        self,
        interval="5minute",
        lookback_days=5,
    ):
        """
        Fetch historical NIFTY 50 candles.

        Returns normalized OHLCV dictionaries for
        the Indicator Engine.
        """

        instrument_token = (
            self.get_nifty_token()
        )

        to_date = datetime.now()

        from_date = (
            to_date
            - timedelta(
                days=int(lookback_days)
            )
        )

        candles = self.kite.historical_data(
            instrument_token=instrument_token,
            from_date=from_date,
            to_date=to_date,
            interval=interval,
            continuous=False,
            oi=False,
        )

        if not candles:
            raise RuntimeError(
                "No historical NIFTY candles returned"
            )

        normalized = []

        for candle in candles:

            normalized.append({
                "date": candle["date"],
                "open": float(candle["open"]),
                "high": float(candle["high"]),
                "low": float(candle["low"]),
                "close": float(candle["close"]),
                "volume": int(
                    candle.get("volume", 0) or 0
                ),
            })

        return normalized


    # --------------------------------------------------------
    # INDIA VIX
    # --------------------------------------------------------

    def get_india_vix(self):
        """
        Fetch the latest India VIX value from Kite.
        """

        data = self.kite.ltp(
            "NSE:INDIA VIX"
        )

        if "NSE:INDIA VIX" not in data:
            raise RuntimeError(
                "India VIX data not returned by Kite"
            )

        vix = float(
            data["NSE:INDIA VIX"]["last_price"]
        )

        if vix <= 0:
            raise RuntimeError(
                "Invalid India VIX value returned by Kite"
            )

        return vix