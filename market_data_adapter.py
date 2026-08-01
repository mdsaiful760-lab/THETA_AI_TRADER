# ============================================================
# THETA AI TRADER — MARKET DATA ADAPTER
# ============================================================

from datetime import date, datetime, timezone


class MarketDataAdapter:
    """
    Converts Zerodha/Kite-shaped instrument and quote data into
    normalized option-contract records.

    IMPORTANT
    ---------
    This adapter:

    - DOES NOT authenticate with Zerodha
    - DOES NOT read API credentials
    - DOES NOT place orders
    - DOES NOT calculate position size
    - DOES NOT allocate risk
    - DOES NOT select a contract
    - DOES NOT calculate Greeks

    Its responsibility is:

        Instrument Master
              +
        Live Quote Data
              +
        Optional Greeks Data
              ↓
        Normalized Option Contracts
              ↓
        OptionContractSelector

    The output format is compatible with
    OptionContractSelector.
    """

    VALID_OPTION_TYPES = {
        "CE",
        "PE",
    }

    VALID_EXCHANGES = {
        "NFO",
        "BFO",
    }

    # ========================================================
    # SAFE CONVERSION HELPERS
    # ========================================================

    def _safe_float(
        self,
        value,
        default=None,
    ):

        try:

            if value is None:
                return default

            return float(value)

        except (
            TypeError,
            ValueError,
        ):

            return default

    def _safe_int(
        self,
        value,
        default=None,
    ):

        try:

            if value is None:
                return default

            return int(value)

        except (
            TypeError,
            ValueError,
        ):

            return default

    def _normalize_text(
        self,
        value,
    ):

        if value is None:
            return ""

        return str(
            value
        ).strip().upper()

    # ========================================================
    # DATE NORMALIZATION
    # ========================================================

    def _normalize_expiry(
        self,
        value,
    ):

        if value is None:
            return None

        if isinstance(
            value,
            datetime,
        ):

            return value.date().isoformat()

        if isinstance(
            value,
            date,
        ):

            return value.isoformat()

        text = str(
            value
        ).strip()

        if not text:
            return None

        formats = [
            "%Y-%m-%d",
            "%d-%m-%Y",
            "%Y/%m/%d",
            "%d/%m/%Y",
        ]

        for fmt in formats:

            try:

                parsed = datetime.strptime(
                    text,
                    fmt,
                )

                return (
                    parsed.date().isoformat()
                )

            except ValueError:
                continue

        return text

    # ========================================================
    # TIMESTAMP NORMALIZATION
    # ========================================================

    def _normalize_timestamp(
        self,
        value,
    ):

        if value is None:
            return None

        if isinstance(
            value,
            datetime,
        ):

            if value.tzinfo is None:

                value = value.replace(
                    tzinfo=timezone.utc
                )

            return value.isoformat()

        return str(
            value
        ).strip() or None

    # ========================================================
    # INSTRUMENT KEY
    # ========================================================

    def build_quote_key(
        self,
        exchange,
        tradingsymbol,
    ):

        exchange = self._normalize_text(
            exchange
        )

        tradingsymbol = (
            self._normalize_text(
                tradingsymbol
            )
        )

        if (
            not exchange
            or not tradingsymbol
        ):

            return None

        return (
            f"{exchange}:{tradingsymbol}"
        )

    # ========================================================
    # NORMALIZE INSTRUMENT MASTER RECORD
    # ========================================================

    def normalize_instrument(
        self,
        instrument,
    ):

        if not isinstance(
            instrument,
            dict,
        ):

            return {
                "valid": False,
                "errors": [
                    "INVALID_INSTRUMENT_OBJECT"
                ],
                "instrument": None,
            }

        underlying = (
            self._normalize_text(
                instrument.get(
                    "name"
                )
            )
        )

        exchange = (
            self._normalize_text(
                instrument.get(
                    "exchange"
                )
            )
        )

        tradingsymbol = (
            self._normalize_text(
                instrument.get(
                    "tradingsymbol"
                )
            )
        )

        option_type = (
            self._normalize_text(
                instrument.get(
                    "instrument_type"
                )
            )
        )

        expiry = (
            self._normalize_expiry(
                instrument.get(
                    "expiry"
                )
            )
        )

        strike = self._safe_float(
            instrument.get(
                "strike"
            )
        )

        lot_size = self._safe_int(
            instrument.get(
                "lot_size"
            )
        )

        instrument_token = (
            instrument.get(
                "instrument_token"
            )
        )

        exchange_token = (
            instrument.get(
                "exchange_token"
            )
        )

        tick_size = self._safe_float(
            instrument.get(
                "tick_size"
            )
        )

        errors = []

        if not underlying:

            errors.append(
                "MISSING_UNDERLYING"
            )

        if (
            exchange
            not in self.VALID_EXCHANGES
        ):

            errors.append(
                "INVALID_EXCHANGE"
            )

        if not tradingsymbol:

            errors.append(
                "MISSING_TRADINGSYMBOL"
            )

        if (
            option_type
            not in self.VALID_OPTION_TYPES
        ):

            errors.append(
                "INVALID_OPTION_TYPE"
            )

        if not expiry:

            errors.append(
                "MISSING_EXPIRY"
            )

        if (
            strike is None
            or strike <= 0
        ):

            errors.append(
                "INVALID_STRIKE"
            )

        if (
            lot_size is None
            or lot_size <= 0
        ):

            errors.append(
                "INVALID_LOT_SIZE"
            )

        if instrument_token is None:

            errors.append(
                "MISSING_INSTRUMENT_TOKEN"
            )

        if (
            tick_size is not None
            and tick_size <= 0
        ):

            errors.append(
                "INVALID_TICK_SIZE"
            )

        normalized = {
            "underlying":
                underlying,

            "exchange":
                exchange,

            "tradingsymbol":
                tradingsymbol,

            "expiry":
                expiry,

            "strike":
                strike,

            "option_type":
                option_type,

            "lot_size":
                lot_size,

            "instrument_token":
                instrument_token,

            "exchange_token":
                exchange_token,

            "tick_size":
                tick_size,

            "quote_key":
                self.build_quote_key(
                    exchange,
                    tradingsymbol,
                ),
        }

        return {
            "valid":
                len(errors) == 0,

            "errors":
                errors,

            "instrument":
                normalized,
        }

    # ========================================================
    # BEST BID / ASK
    # ========================================================

    def _extract_best_bid(
        self,
        quote,
    ):

        try:

            buy_depth = (
                quote.get(
                    "depth",
                    {}
                ).get(
                    "buy",
                    []
                )
            )

            if not buy_depth:
                return None

            prices = []

            for level in buy_depth:

                if not isinstance(
                    level,
                    dict,
                ):
                    continue

                price = self._safe_float(
                    level.get(
                        "price"
                    )
                )

                if (
                    price is not None
                    and price > 0
                ):

                    prices.append(
                        price
                    )

            if not prices:
                return None

            return max(
                prices
            )

        except Exception:

            return None

    def _extract_best_ask(
        self,
        quote,
    ):

        try:

            sell_depth = (
                quote.get(
                    "depth",
                    {}
                ).get(
                    "sell",
                    []
                )
            )

            if not sell_depth:
                return None

            prices = []

            for level in sell_depth:

                if not isinstance(
                    level,
                    dict,
                ):
                    continue

                price = self._safe_float(
                    level.get(
                        "price"
                    )
                )

                if (
                    price is not None
                    and price > 0
                ):

                    prices.append(
                        price
                    )

            if not prices:
                return None

            return min(
                prices
            )

        except Exception:

            return None

    # ========================================================
    # QUOTE NORMALIZATION
    # ========================================================

    def normalize_quote(
        self,
        quote,
    ):

        if not isinstance(
            quote,
            dict,
        ):

            return {
                "valid": False,
                "errors": [
                    "INVALID_QUOTE_OBJECT"
                ],
                "quote": None,
            }

        ltp = self._safe_float(
            quote.get(
                "last_price"
            )
        )

        volume = self._safe_int(
            quote.get(
                "volume"
            ),
            default=0,
        )

        open_interest = self._safe_int(
            quote.get(
                "oi"
            ),
            default=0,
        )

        bid = self._extract_best_bid(
            quote
        )

        ask = self._extract_best_ask(
            quote
        )

        timestamp = (
            self._normalize_timestamp(
                quote.get(
                    "timestamp"
                )
                or quote.get(
                    "last_trade_time"
                )
            )
        )

        errors = []

        if (
            ltp is None
            or ltp <= 0
        ):

            errors.append(
                "INVALID_LTP"
            )

        if volume < 0:

            errors.append(
                "INVALID_VOLUME"
            )

        if open_interest < 0:

            errors.append(
                "INVALID_OPEN_INTEREST"
            )

        if (
            bid is None
            or bid <= 0
        ):

            errors.append(
                "MISSING_BID"
            )

        if (
            ask is None
            or ask <= 0
        ):

            errors.append(
                "MISSING_ASK"
            )

        if (
            bid is not None
            and ask is not None
            and ask < bid
        ):

            errors.append(
                "INVERTED_MARKET"
            )

        normalized = {
            "ltp":
                ltp,

            "bid":
                bid,

            "ask":
                ask,

            "volume":
                volume,

            "open_interest":
                open_interest,

            "timestamp":
                timestamp,

            "last_quantity":
                self._safe_int(
                    quote.get(
                        "last_quantity"
                    ),
                    default=0,
                ),

            "average_price":
                self._safe_float(
                    quote.get(
                        "average_price"
                    )
                ),

            "buy_quantity":
                self._safe_int(
                    quote.get(
                        "buy_quantity"
                    ),
                    default=0,
                ),

            "sell_quantity":
                self._safe_int(
                    quote.get(
                        "sell_quantity"
                    ),
                    default=0,
                ),

            "oi_day_high":
                self._safe_int(
                    quote.get(
                        "oi_day_high"
                    ),
                    default=0,
                ),

            "oi_day_low":
                self._safe_int(
                    quote.get(
                        "oi_day_low"
                    ),
                    default=0,
                ),
        }

        return {
            "valid":
                len(errors) == 0,

            "errors":
                errors,

            "quote":
                normalized,
        }

    # ========================================================
    # NORMALIZE OPTIONAL GREEKS
    # ========================================================

    def normalize_greeks(
        self,
        greeks,
    ):

        if greeks is None:

            return {
                "delta": None,
                "iv": None,
                "gamma": None,
                "theta": None,
                "vega": None,
            }

        if not isinstance(
            greeks,
            dict,
        ):

            return {
                "delta": None,
                "iv": None,
                "gamma": None,
                "theta": None,
                "vega": None,
            }

        return {
            "delta":
                self._safe_float(
                    greeks.get(
                        "delta"
                    )
                ),

            "iv":
                self._safe_float(
                    greeks.get(
                        "iv"
                    )
                ),

            "gamma":
                self._safe_float(
                    greeks.get(
                        "gamma"
                    )
                ),

            "theta":
                self._safe_float(
                    greeks.get(
                        "theta"
                    )
                ),

            "vega":
                self._safe_float(
                    greeks.get(
                        "vega"
                    )
                ),
        }

    # ========================================================
    # BUILD ONE NORMALIZED CONTRACT
    # ========================================================

    def build_contract(
        self,
        instrument,
        quote,
        greeks=None,
    ):

        instrument_result = (
            self.normalize_instrument(
                instrument
            )
        )

        if not instrument_result[
            "valid"
        ]:

            return {
                "valid": False,

                "reason":
                    "INVALID_INSTRUMENT",

                "errors":
                    list(
                        instrument_result[
                            "errors"
                        ]
                    ),

                "contract":
                    None,
            }

        quote_result = (
            self.normalize_quote(
                quote
            )
        )

        if not quote_result[
            "valid"
        ]:

            return {
                "valid": False,

                "reason":
                    "INVALID_QUOTE",

                "errors":
                    list(
                        quote_result[
                            "errors"
                        ]
                    ),

                "contract":
                    None,
            }

        normalized_instrument = (
            instrument_result[
                "instrument"
            ]
        )

        normalized_quote = (
            quote_result[
                "quote"
            ]
        )

        normalized_greeks = (
            self.normalize_greeks(
                greeks
            )
        )

        contract = {
            # ------------------------------------------------
            # CONTRACT IDENTITY
            # ------------------------------------------------

            "underlying":
                normalized_instrument[
                    "underlying"
                ],

            "exchange":
                normalized_instrument[
                    "exchange"
                ],

            "tradingsymbol":
                normalized_instrument[
                    "tradingsymbol"
                ],

            "expiry":
                normalized_instrument[
                    "expiry"
                ],

            "strike":
                normalized_instrument[
                    "strike"
                ],

            "option_type":
                normalized_instrument[
                    "option_type"
                ],

            "lot_size":
                normalized_instrument[
                    "lot_size"
                ],

            # ------------------------------------------------
            # LIVE MARKET DATA
            # ------------------------------------------------

            "ltp":
                normalized_quote[
                    "ltp"
                ],

            "bid":
                normalized_quote[
                    "bid"
                ],

            "ask":
                normalized_quote[
                    "ask"
                ],

            "volume":
                normalized_quote[
                    "volume"
                ],

            "open_interest":
                normalized_quote[
                    "open_interest"
                ],

            # ------------------------------------------------
            # GREEKS
            # ------------------------------------------------

            "delta":
                normalized_greeks[
                    "delta"
                ],

            "iv":
                normalized_greeks[
                    "iv"
                ],

            "gamma":
                normalized_greeks[
                    "gamma"
                ],

            "theta":
                normalized_greeks[
                    "theta"
                ],

            "vega":
                normalized_greeks[
                    "vega"
                ],

            # ------------------------------------------------
            # BROKER METADATA
            # ------------------------------------------------

            "instrument_token":
                normalized_instrument[
                    "instrument_token"
                ],

            "exchange_token":
                normalized_instrument[
                    "exchange_token"
                ],

            "tick_size":
                normalized_instrument[
                    "tick_size"
                ],

            # ------------------------------------------------
            # QUOTE METADATA
            # ------------------------------------------------

            "quote_timestamp":
                normalized_quote[
                    "timestamp"
                ],

            "last_quantity":
                normalized_quote[
                    "last_quantity"
                ],

            "average_price":
                normalized_quote[
                    "average_price"
                ],

            "buy_quantity":
                normalized_quote[
                    "buy_quantity"
                ],

            "sell_quantity":
                normalized_quote[
                    "sell_quantity"
                ],

            "oi_day_high":
                normalized_quote[
                    "oi_day_high"
                ],

            "oi_day_low":
                normalized_quote[
                    "oi_day_low"
                ],
        }

        return {
            "valid":
                True,

            "reason":
                "CONTRACT_NORMALIZED",

            "errors":
                [],

            "contract":
                contract,
        }

    # ========================================================
    # GREEKS LOOKUP
    # ========================================================

    def _find_greeks(
        self,
        greeks_map,
        quote_key,
        tradingsymbol,
        instrument_token,
    ):

        if not isinstance(
            greeks_map,
            dict,
        ):

            return None

        possible_keys = [
            quote_key,
            tradingsymbol,
            instrument_token,
            str(
                instrument_token
            )
            if instrument_token
            is not None
            else None,
        ]

        for key in possible_keys:

            if (
                key is not None
                and key in greeks_map
            ):

                return greeks_map[
                    key
                ]

        return None

    # ========================================================
    # BUILD NORMALIZED OPTION CHAIN
    # ========================================================

    def build_option_chain(
        self,
        instruments,
        quotes,
        underlying,
        expiry=None,
        exchange=None,
        greeks_map=None,
        option_types=None,
    ):
        """
        Build normalized option contracts from instrument master
        and Kite quote dictionaries.

        Parameters
        ----------
        instruments:
            Iterable returned from kite.instruments(...)

        quotes:
            Dictionary returned from kite.quote([...])

            Example:
            {
                "NFO:NIFTY...CE": {...},
                "NFO:NIFTY...PE": {...}
            }

        underlying:
            NIFTY, BANKNIFTY, SENSEX etc.

        expiry:
            Optional exact expiry filter.

        exchange:
            Optional exchange filter such as NFO or BFO.

        greeks_map:
            Optional dictionary containing calculated Greeks.

        option_types:
            Optional CE/PE filter.
        """

        underlying = (
            self._normalize_text(
                underlying
            )
        )

        normalized_expiry = (
            self._normalize_expiry(
                expiry
            )
            if expiry is not None
            else None
        )

        normalized_exchange = (
            self._normalize_text(
                exchange
            )
            if exchange is not None
            else None
        )

        if option_types is None:

            requested_option_types = {
                "CE",
                "PE",
            }

        else:

            requested_option_types = {
                self._normalize_text(
                    item
                )
                for item in option_types
            }

        # ====================================================
        # REQUEST VALIDATION
        # ====================================================

        validation_errors = []

        if not underlying:

            validation_errors.append(
                "UNDERLYING_REQUIRED"
            )

        if (
            normalized_exchange is not None
            and normalized_exchange
            not in self.VALID_EXCHANGES
        ):

            validation_errors.append(
                "INVALID_EXCHANGE"
            )

        if not requested_option_types:

            validation_errors.append(
                "OPTION_TYPES_REQUIRED"
            )

        invalid_types = (
            requested_option_types
            - self.VALID_OPTION_TYPES
        )

        if invalid_types:

            validation_errors.append(
                "INVALID_OPTION_TYPES"
            )

        if instruments is None:

            validation_errors.append(
                "INSTRUMENTS_REQUIRED"
            )

        if not isinstance(
            quotes,
            dict,
        ):

            validation_errors.append(
                "QUOTES_MUST_BE_DICTIONARY"
            )

        if validation_errors:

            return {
                "adapter_permission":
                    "BLOCK",

                "adapter_allowed":
                    False,

                "reason":
                    "INVALID_ADAPTER_REQUEST",

                "validation_errors":
                    validation_errors,

                "underlying":
                    underlying,

                "expiry":
                    normalized_expiry,

                "exchange":
                    normalized_exchange,

                "instrument_count":
                    0,

                "matched_instruments":
                    0,

                "normalized_count":
                    0,

                "rejected_count":
                    0,

                "contracts":
                    [],

                "rejections":
                    [],

                "broker_order_allowed":
                    False,
            }

        # ====================================================
        # CONVERT INSTRUMENTS TO LIST
        # ====================================================

        try:

            instrument_list = list(
                instruments
            )

        except TypeError:

            return {
                "adapter_permission":
                    "BLOCK",

                "adapter_allowed":
                    False,

                "reason":
                    "INVALID_INSTRUMENT_COLLECTION",

                "validation_errors": [
                    "INSTRUMENTS_MUST_BE_ITERABLE"
                ],

                "underlying":
                    underlying,

                "expiry":
                    normalized_expiry,

                "exchange":
                    normalized_exchange,

                "instrument_count":
                    0,

                "matched_instruments":
                    0,

                "normalized_count":
                    0,

                "rejected_count":
                    0,

                "contracts":
                    [],

                "rejections":
                    [],

                "broker_order_allowed":
                    False,
            }

        # ====================================================
        # BUILD CHAIN
        # ====================================================

        contracts = []
        rejections = []

        matched_instruments = 0

        seen_quote_keys = set()

        for raw_instrument in instrument_list:

            instrument_result = (
                self.normalize_instrument(
                    raw_instrument
                )
            )

            if not instrument_result[
                "valid"
            ]:

                rejections.append(
                    {
                        "tradingsymbol":
                            (
                                raw_instrument.get(
                                    "tradingsymbol"
                                )
                                if isinstance(
                                    raw_instrument,
                                    dict,
                                )
                                else None
                            ),

                        "reason":
                            "INVALID_INSTRUMENT",

                        "errors":
                            list(
                                instrument_result[
                                    "errors"
                                ]
                            ),
                    }
                )

                continue

            instrument = (
                instrument_result[
                    "instrument"
                ]
            )

            # ------------------------------------------------
            # FILTER UNDERLYING
            # ------------------------------------------------

            if (
                instrument[
                    "underlying"
                ]
                != underlying
            ):

                continue

            # ------------------------------------------------
            # FILTER OPTION TYPE
            # ------------------------------------------------

            if (
                instrument[
                    "option_type"
                ]
                not in requested_option_types
            ):

                continue

            # ------------------------------------------------
            # FILTER EXPIRY
            # ------------------------------------------------

            if (
                normalized_expiry
                is not None
                and instrument[
                    "expiry"
                ]
                != normalized_expiry
            ):

                continue

            # ------------------------------------------------
            # FILTER EXCHANGE
            # ------------------------------------------------

            if (
                normalized_exchange
                is not None
                and instrument[
                    "exchange"
                ]
                != normalized_exchange
            ):

                continue

            matched_instruments += 1

            quote_key = (
                instrument[
                    "quote_key"
                ]
            )

            # ------------------------------------------------
            # DUPLICATE PROTECTION
            # ------------------------------------------------

            if quote_key in seen_quote_keys:

                rejections.append(
                    {
                        "tradingsymbol":
                            instrument[
                                "tradingsymbol"
                            ],

                        "reason":
                            "DUPLICATE_INSTRUMENT",

                        "errors": [
                            "DUPLICATE_QUOTE_KEY"
                        ],
                    }
                )

                continue

            seen_quote_keys.add(
                quote_key
            )

            # ------------------------------------------------
            # QUOTE LOOKUP
            # ------------------------------------------------

            quote = quotes.get(
                quote_key
            )

            if quote is None:

                rejections.append(
                    {
                        "tradingsymbol":
                            instrument[
                                "tradingsymbol"
                            ],

                        "reason":
                            "QUOTE_NOT_FOUND",

                        "errors": [
                            "MISSING_QUOTE"
                        ],
                    }
                )

                continue

            # ------------------------------------------------
            # OPTIONAL GREEKS LOOKUP
            # ------------------------------------------------

            greeks = self._find_greeks(
                greeks_map=greeks_map,
                quote_key=quote_key,
                tradingsymbol=instrument[
                    "tradingsymbol"
                ],
                instrument_token=instrument[
                    "instrument_token"
                ],
            )

            # ------------------------------------------------
            # NORMALIZE CONTRACT
            # ------------------------------------------------

            build_result = (
                self.build_contract(
                    instrument=raw_instrument,
                    quote=quote,
                    greeks=greeks,
                )
            )

            if not build_result[
                "valid"
            ]:

                rejections.append(
                    {
                        "tradingsymbol":
                            instrument[
                                "tradingsymbol"
                            ],

                        "reason":
                            build_result[
                                "reason"
                            ],

                        "errors":
                            list(
                                build_result[
                                    "errors"
                                ]
                            ),
                    }
                )

                continue

            contracts.append(
                build_result[
                    "contract"
                ]
            )

        # ====================================================
        # NO CONTRACTS
        # ====================================================

        if not contracts:

            return {
                "adapter_permission":
                    "BLOCK",

                "adapter_allowed":
                    False,

                "reason":
                    "NO_VALID_OPTION_CONTRACTS",

                "validation_errors":
                    [],

                "underlying":
                    underlying,

                "expiry":
                    normalized_expiry,

                "exchange":
                    normalized_exchange,

                "instrument_count":
                    len(
                        instrument_list
                    ),

                "matched_instruments":
                    matched_instruments,

                "normalized_count":
                    0,

                "rejected_count":
                    len(
                        rejections
                    ),

                "contracts":
                    [],

                "rejections":
                    rejections,

                "broker_order_allowed":
                    False,
            }

        # ====================================================
        # DETERMINISTIC SORT
        # ====================================================

        contracts.sort(
            key=lambda item: (
                item[
                    "expiry"
                ],

                item[
                    "strike"
                ],

                item[
                    "option_type"
                ],

                item[
                    "tradingsymbol"
                ],
            )
        )

        # ====================================================
        # SUCCESS
        # ====================================================

        return {
            "adapter_permission":
                "ALLOW",

            "adapter_allowed":
                True,

            "reason":
                "OPTION_CHAIN_NORMALIZED",

            "validation_errors":
                [],

            "underlying":
                underlying,

            "expiry":
                normalized_expiry,

            "exchange":
                normalized_exchange,

            "instrument_count":
                len(
                    instrument_list
                ),

            "matched_instruments":
                matched_instruments,

            "normalized_count":
                len(
                    contracts
                ),

            "rejected_count":
                len(
                    rejections
                ),

            "contracts":
                contracts,

            "rejections":
                rejections,

            # Adapter can never place an order.
            "broker_order_allowed":
                False,
        }

    # ========================================================
    # FIND AVAILABLE EXPIRIES
    # ========================================================

    def get_available_expiries(
        self,
        instruments,
        underlying,
        exchange=None,
        include_expired=False,
        reference_date=None,
    ):

        underlying = (
            self._normalize_text(
                underlying
            )
        )

        exchange = (
            self._normalize_text(
                exchange
            )
            if exchange is not None
            else None
        )

        if reference_date is None:

            reference_date = (
                date.today()
            )

        if isinstance(
            reference_date,
            datetime,
        ):

            reference_date = (
                reference_date.date()
            )

        expiries = set()

        for raw in instruments:

            result = (
                self.normalize_instrument(
                    raw
                )
            )

            if not result[
                "valid"
            ]:

                continue

            instrument = (
                result[
                    "instrument"
                ]
            )

            if (
                instrument[
                    "underlying"
                ]
                != underlying
            ):

                continue

            if (
                exchange is not None
                and instrument[
                    "exchange"
                ]
                != exchange
            ):

                continue

            expiry_text = (
                instrument[
                    "expiry"
                ]
            )

            try:

                expiry_date = (
                    datetime.strptime(
                        expiry_text,
                        "%Y-%m-%d",
                    ).date()
                )

            except ValueError:

                continue

            if (
                not include_expired
                and expiry_date
                < reference_date
            ):

                continue

            expiries.add(
                expiry_date
            )

        return sorted(
            expiries
        )

    # ========================================================
    # FIND NEAREST EXPIRY
    # ========================================================

    def get_nearest_expiry(
        self,
        instruments,
        underlying,
        exchange=None,
        reference_date=None,
    ):

        expiries = (
            self.get_available_expiries(
                instruments=instruments,
                underlying=underlying,
                exchange=exchange,
                include_expired=False,
                reference_date=reference_date,
            )
        )

        if not expiries:

            return None

        return expiries[0]

    # ========================================================
    # FIND STRIKES
    # ========================================================

    def get_available_strikes(
        self,
        instruments,
        underlying,
        expiry,
        exchange=None,
    ):

        underlying = (
            self._normalize_text(
                underlying
            )
        )

        expiry = (
            self._normalize_expiry(
                expiry
            )
        )

        exchange = (
            self._normalize_text(
                exchange
            )
            if exchange is not None
            else None
        )

        strikes = set()

        for raw in instruments:

            result = (
                self.normalize_instrument(
                    raw
                )
            )

            if not result[
                "valid"
            ]:

                continue

            instrument = (
                result[
                    "instrument"
                ]
            )

            if (
                instrument[
                    "underlying"
                ]
                != underlying
            ):

                continue

            if (
                instrument[
                    "expiry"
                ]
                != expiry
            ):

                continue

            if (
                exchange is not None
                and instrument[
                    "exchange"
                ]
                != exchange
            ):

                continue

            strikes.add(
                instrument[
                    "strike"
                ]
            )

        return sorted(
            strikes
        )

    # ========================================================
    # FIND ATM STRIKE
    # ========================================================

    def get_atm_strike(
        self,
        instruments,
        underlying,
        expiry,
        spot_price,
        exchange=None,
    ):

        spot_price = (
            self._safe_float(
                spot_price
            )
        )

        if (
            spot_price is None
            or spot_price <= 0
        ):

            return None

        strikes = (
            self.get_available_strikes(
                instruments=instruments,
                underlying=underlying,
                expiry=expiry,
                exchange=exchange,
            )
        )

        if not strikes:

            return None

        return min(
            strikes,
            key=lambda strike: (
                abs(
                    strike
                    - spot_price
                ),
                strike,
            ),
        )

    # ========================================================
    # STRIKE STEP — INFORMATIONAL ONLY
    # ========================================================

    def detect_strike_step(
        self,
        strikes,
    ):

        try:

            clean = sorted({
                float(
                    strike
                )
                for strike in strikes
                if float(
                    strike
                ) > 0
            })

        except (
            TypeError,
            ValueError,
        ):

            return None

        if len(clean) < 2:

            return None

        differences = [
            clean[index + 1]
            - clean[index]
            for index
            in range(
                len(clean) - 1
            )
            if (
                clean[index + 1]
                > clean[index]
            )
        ]

        if not differences:

            return None

        return min(
            differences
        )

    # ========================================================
    # NEARBY STRIKES
    # ========================================================

    def get_nearby_strikes(
        self,
        strikes,
        spot_price,
        strikes_each_side=10,
    ):

        spot_price = (
            self._safe_float(
                spot_price
            )
        )

        strikes_each_side = (
            self._safe_int(
                strikes_each_side,
                default=10,
            )
        )

        if (
            spot_price is None
            or spot_price <= 0
            or strikes_each_side < 0
        ):

            return []

        try:

            clean = sorted({
                float(
                    strike
                )
                for strike in strikes
                if float(
                    strike
                ) > 0
            })

        except (
            TypeError,
            ValueError,
        ):

            return []

        if not clean:

            return []

        atm_index = min(
            range(
                len(clean)
            ),
            key=lambda index: (
                abs(
                    clean[index]
                    - spot_price
                ),
                clean[index],
            ),
        )

        start = max(
            0,
            atm_index
            - strikes_each_side,
        )

        end = min(
            len(clean),
            atm_index
            + strikes_each_side
            + 1,
        )

        return clean[
            start:end
        ]