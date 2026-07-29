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



    # --------------------------------------------------------
    # NIFTY OPTION CHAIN SNAPSHOT
    # --------------------------------------------------------

    def get_nifty_option_snapshot(
        self,
        strike_range=10,
    ):
        """
        Fetch a normalized NIFTY option-chain snapshot
        around the current ATM strike.

        Returns:
        - Spot
        - Nearest expiry
        - ATM strike
        - Option price
        - Open Interest
        - Volume
        - Bid / Ask
        """

        from datetime import date

        # ----------------------------------------------------
        # GET NIFTY SPOT
        # ----------------------------------------------------

        spot_data = self.kite.ltp(
            "NSE:NIFTY 50"
        )

        spot = float(
            spot_data["NSE:NIFTY 50"][
                "last_price"
            ]
        )

        # ----------------------------------------------------
        # LOAD NFO INSTRUMENTS
        # ----------------------------------------------------

        instruments = self.kite.instruments(
            "NFO"
        )

        nifty_options = [
            instrument
            for instrument in instruments
            if instrument.get("name") == "NIFTY"
            and instrument.get(
                "instrument_type"
            ) in ("CE", "PE")
        ]

        if not nifty_options:
            raise RuntimeError(
                "No NIFTY options found"
            )

        # ----------------------------------------------------
        # FIND NEAREST EXPIRY
        # ----------------------------------------------------

        today = date.today()

        expiries = sorted({
            instrument["expiry"]
            for instrument in nifty_options
            if instrument["expiry"] >= today
        })

        if not expiries:
            raise RuntimeError(
                "No future NIFTY expiry found"
            )

        expiry = expiries[0]

        expiry_options = [
            instrument
            for instrument in nifty_options
            if instrument["expiry"] == expiry
        ]

        # ----------------------------------------------------
        # FIND ATM
        # ----------------------------------------------------

        strikes = sorted({
            float(instrument["strike"])
            for instrument in expiry_options
        })

        if len(strikes) < 2:
            raise RuntimeError(
                "Not enough NIFTY strikes found"
            )

        atm = min(
            strikes,
            key=lambda strike: abs(
                strike - spot
            ),
        )

        # ----------------------------------------------------
        # DETERMINE STRIKE STEP
        # ----------------------------------------------------

        strike_steps = [
            strikes[index + 1]
            - strikes[index]
            for index in range(
                len(strikes) - 1
            )
            if (
                strikes[index + 1]
                > strikes[index]
            )
        ]

        strike_step = min(
            strike_steps
        )

        minimum_strike = (
            atm
            - strike_range * strike_step
        )

        maximum_strike = (
            atm
            + strike_range * strike_step
        )

        selected_instruments = [
            instrument
            for instrument in expiry_options
            if (
                minimum_strike
                <= float(
                    instrument["strike"]
                )
                <= maximum_strike
            )
        ]

        # ----------------------------------------------------
        # FETCH LIVE QUOTES
        # ----------------------------------------------------

        symbols = [
            (
                "NFO:"
                + instrument["tradingsymbol"]
            )
            for instrument
            in selected_instruments
        ]

        if not symbols:
            raise RuntimeError(
                "No option symbols selected"
            )

        quotes = self.kite.quote(
            symbols
        )

        # ----------------------------------------------------
        # NORMALIZE DATA
        # ----------------------------------------------------

        options = []

        for instrument in selected_instruments:

            key = (
                "NFO:"
                + instrument["tradingsymbol"]
            )

            quote = quotes.get(
                key,
                {}
            )

            depth = quote.get(
                "depth",
                {}
            )

            buy_depth = depth.get(
                "buy",
                []
            )

            sell_depth = depth.get(
                "sell",
                []
            )

            best_bid = (
                float(
                    buy_depth[0].get(
                        "price",
                        0,
                    )
                )
                if buy_depth
                else 0.0
            )

            best_ask = (
                float(
                    sell_depth[0].get(
                        "price",
                        0,
                    )
                )
                if sell_depth
                else 0.0
            )

            options.append({
                "strike": float(
                    instrument["strike"]
                ),
                "option_type": (
                    instrument[
                        "instrument_type"
                    ]
                ),
                "symbol": (
                    instrument[
                        "tradingsymbol"
                    ]
                ),
                "price": float(
                    quote.get(
                        "last_price",
                        0,
                    ) or 0
                ),
                "oi": int(
                    quote.get(
                        "oi",
                        0,
                    ) or 0
                ),
                "volume": int(
                    quote.get(
                        "volume",
                        0,
                    ) or 0
                ),
                "bid": best_bid,
                "ask": best_ask,
            })

        return {
            "spot": spot,
            "expiry": expiry,
            "atm": atm,
            "strike_step": strike_step,
            "options": options,
        }