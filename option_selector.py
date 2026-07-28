# ============================================================
# THETA AI TRADER — OPTION & STRIKE SELECTION ENGINE
# ============================================================

from datetime import date, datetime


class OptionSelector:
    """
    Handles option-contract discovery and later strike selection.

    IMPORTANT:
    This class does NOT place orders.
    """

    def __init__(self):
        self.reasons = []

    # --------------------------------------------------------
    # RESET
    # --------------------------------------------------------

    def reset(self):
        self.reasons = []

    # --------------------------------------------------------
    # NORMALIZE EXPIRY
    # --------------------------------------------------------

    def normalize_expiry(self, expiry):
        """
        Convert expiry value to a date object.
        """

        if isinstance(expiry, datetime):
            return expiry.date()

        if isinstance(expiry, date):
            return expiry

        if isinstance(expiry, str):
            return datetime.strptime(
                expiry,
                "%Y-%m-%d",
            ).date()

        raise ValueError(
            f"Unsupported expiry format: {expiry}"
        )

    # --------------------------------------------------------
    # FILTER NIFTY OPTIONS
    # --------------------------------------------------------

    def filter_nifty_options(self, instruments):
        """
        Keep only NIFTY index CE/PE option contracts.
        """

        if not instruments:
            raise ValueError(
                "Instrument list is empty"
            )

        nifty_options = []

        for instrument in instruments:

            name = str(
                instrument.get("name", "")
            ).upper()

            segment = str(
                instrument.get("segment", "")
            ).upper()

            instrument_type = str(
                instrument.get(
                    "instrument_type",
                    "",
                )
            ).upper()

            expiry = instrument.get("expiry")

            strike = instrument.get("strike")

            trading_symbol = instrument.get(
                "tradingsymbol"
            )

            instrument_token = instrument.get(
                "instrument_token"
            )

            # Exact name check prevents BANKNIFTY,
            # FINNIFTY, etc. from entering this list.
            if name != "NIFTY":
                continue

            if segment != "NFO-OPT":
                continue

            if instrument_type not in (
                "CE",
                "PE",
            ):
                continue

            if expiry is None:
                continue

            if strike is None:
                continue

            if not trading_symbol:
                continue

            if instrument_token is None:
                continue

            try:
                expiry_date = (
                    self.normalize_expiry(expiry)
                )

                strike_value = float(strike)

            except (TypeError, ValueError):
                continue

            nifty_options.append(
                {
                    "instrument_token": (
                        instrument_token
                    ),
                    "tradingsymbol": (
                        trading_symbol
                    ),
                    "expiry": expiry_date,
                    "strike": strike_value,
                    "option_type": (
                        instrument_type
                    ),
                }
            )

        if not nifty_options:
            raise ValueError(
                "No valid NIFTY option contracts found"
            )

        self.reasons.append(
            f"Found {len(nifty_options)} "
            f"valid NIFTY option contracts"
        )

        return nifty_options

    # --------------------------------------------------------
    # FIND AVAILABLE EXPIRIES
    # --------------------------------------------------------

    def get_available_expiries(
        self,
        nifty_options,
        today=None,
    ):
        """
        Return non-expired NIFTY expiries.
        """

        if today is None:
            today = date.today()

        if isinstance(today, datetime):
            today = today.date()

        expiries = sorted(
            {
                option["expiry"]
                for option in nifty_options
                if option["expiry"] >= today
            }
        )

        if not expiries:
            raise ValueError(
                "No active NIFTY expiries found"
            )

        return expiries

    # --------------------------------------------------------
    # SELECT NEAREST EXPIRY
    # --------------------------------------------------------

    def select_nearest_expiry(
        self,
        nifty_options,
        today=None,
    ):
        """
        Select the nearest non-expired expiry.
        """

        expiries = self.get_available_expiries(
            nifty_options,
            today=today,
        )

        selected_expiry = expiries[0]

        self.reasons.append(
            f"Nearest active expiry selected: "
            f"{selected_expiry}"
        )

        return selected_expiry

    # --------------------------------------------------------
    # CONTRACTS FOR EXPIRY
    # --------------------------------------------------------

    def contracts_for_expiry(
        self,
        nifty_options,
        expiry,
    ):
        """
        Return contracts belonging to one expiry.
        """

        expiry = self.normalize_expiry(expiry)

        contracts = [
            option
            for option in nifty_options
            if option["expiry"] == expiry
        ]

        contracts.sort(
            key=lambda option: (
                option["strike"],
                option["option_type"],
            )
        )

        if not contracts:
            raise ValueError(
                f"No contracts found for "
                f"expiry {expiry}"
            )

        return contracts

    # --------------------------------------------------------
    # STRIKE WINDOW AROUND SPOT
    # --------------------------------------------------------

    def select_strike_window(
        self,
        contracts,
        spot_price,
        points=500,
    ):
        """
        Keep CE/PE contracts within a specified
        number of points around NIFTY spot.
        """

        if not contracts:
            raise ValueError(
                "No option contracts provided"
            )

        spot_price = float(spot_price)
        points = float(points)

        if spot_price <= 0:
            raise ValueError(
                "Spot price must be greater than zero"
            )

        if points <= 0:
            raise ValueError(
                "Strike window must be greater than zero"
            )

        lower_bound = spot_price - points
        upper_bound = spot_price + points

        selected = [
            contract
            for contract in contracts
            if lower_bound
            <= float(contract["strike"])
            <= upper_bound
        ]

        if not selected:
            raise ValueError(
                "No contracts found inside strike window"
            )

        selected.sort(
            key=lambda contract: (
                contract["strike"],
                contract["option_type"],
            )
        )

        self.reasons.append(
            f"Selected {len(selected)} contracts "
            f"within ±{points:.0f} points of spot"
        )

        return selected


    # --------------------------------------------------------
    # BUILD KITE QUOTE SYMBOLS
    # --------------------------------------------------------

    def build_quote_symbols(self, contracts):
        """
        Convert option contracts into Kite NFO quote symbols.
        """

        if not contracts:
            raise ValueError(
                "No contracts provided for quote lookup"
            )

        symbols = []

        for contract in contracts:

            trading_symbol = contract.get(
                "tradingsymbol"
            )

            if not trading_symbol:
                continue

            symbols.append(
                f"NFO:{trading_symbol}"
            )

        if not symbols:
            raise ValueError(
                "No valid quote symbols generated"
            )

        return symbols

    # --------------------------------------------------------
    # PROCESS LIVE OPTION QUOTES
    # --------------------------------------------------------

    def process_quotes(
        self,
        contracts,
        quotes,
    ):
        """
        Attach live quote and liquidity information
        to each option contract.
        """

        if not contracts:
            raise ValueError(
                "No contracts provided"
            )

        if not isinstance(quotes, dict):
            raise ValueError(
                "Quotes must be a dictionary"
            )

        processed = []

        for contract in contracts:

            trading_symbol = contract["tradingsymbol"]
            quote_key = f"NFO:{trading_symbol}"

            quote = quotes.get(quote_key)

            if not quote:
                continue

            ltp = float(
                quote.get("last_price", 0) or 0
            )

            volume = int(
                quote.get("volume", 0) or 0
            )

            oi = int(
                quote.get("oi", 0) or 0
            )

            depth = quote.get("depth", {}) or {}

            buy_depth = depth.get("buy", []) or []
            sell_depth = depth.get("sell", []) or []

            best_bid = 0.0
            best_ask = 0.0

            if buy_depth:
                best_bid = float(
                    buy_depth[0].get("price", 0) or 0
                )

            if sell_depth:
                best_ask = float(
                    sell_depth[0].get("price", 0) or 0
                )

            spread = 0.0
            spread_pct = None

            if (
                best_bid > 0
                and best_ask > 0
                and best_ask >= best_bid
            ):
                spread = best_ask - best_bid

                midpoint = (
                    best_ask + best_bid
                ) / 2

                if midpoint > 0:
                    spread_pct = (
                        spread / midpoint
                    ) * 100

            processed.append(
                {
                    **contract,
                    "ltp": ltp,
                    "best_bid": best_bid,
                    "best_ask": best_ask,
                    "spread": spread,
                    "spread_pct": spread_pct,
                    "volume": volume,
                    "oi": oi,
                }
            )

        if not processed:
            raise ValueError(
                "No live option quotes could be processed"
            )

        self.reasons.append(
            f"Processed live quotes for "
            f"{len(processed)} contracts"
        )

        return processed


    # --------------------------------------------------------
    # OPTION LIQUIDITY FILTER
    # --------------------------------------------------------

    def filter_liquid_options(
        self,
        options,
        min_volume=100000,
        min_oi=50000,
        max_spread_pct=5.0,
    ):
        """
        Remove option contracts that do not meet
        minimum execution-quality requirements.

        A contract must have:
        - Valid bid and ask
        - Minimum trading volume
        - Minimum open interest
        - Acceptable bid-ask spread
        """

        if not options:
            raise ValueError(
                "No option quotes provided for liquidity filtering"
            )

        liquid_options = []

        for option in options:

            best_bid = float(
                option.get("best_bid", 0) or 0
            )

            best_ask = float(
                option.get("best_ask", 0) or 0
            )

            volume = int(
                option.get("volume", 0) or 0
            )

            oi = int(
                option.get("oi", 0) or 0
            )

            spread_pct = option.get(
                "spread_pct"
            )

            # Bid and ask must both exist
            if best_bid <= 0 or best_ask <= 0:
                continue

            # Reject crossed/invalid market
            if best_ask < best_bid:
                continue

            # Spread must be calculable
            if spread_pct is None:
                continue

            # Minimum volume
            if volume < min_volume:
                continue

            # Minimum open interest
            if oi < min_oi:
                continue

            # Maximum acceptable spread
            if spread_pct > max_spread_pct:
                continue

            liquid_options.append(
                option
            )

        self.reasons.append(
            f"Liquidity filter retained "
            f"{len(liquid_options)} of "
            f"{len(options)} contracts"
        )

        return liquid_options