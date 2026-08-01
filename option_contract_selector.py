# ============================================================
# THETA AI TRADER — OPTION CONTRACT SELECTOR
# ============================================================

from datetime import datetime, date


class OptionContractSelector:
    """
    Selects the best option contract from normalized option
    contract / option-chain data.

    IMPORTANT
    ---------
    This engine:

    - DOES NOT calculate position size
    - DOES NOT allocate rupee risk
    - DOES NOT increase/decrease authorized lots
    - DOES NOT place broker orders
    - DOES NOT connect directly to Zerodha
    - DOES NOT hard-code exchange lot sizes
    - DOES NOT hard-code expiry weekdays

    It only selects the most suitable contract from the
    contracts supplied to it.

    Expected candidate fields
    -------------------------
    {
        "underlying": "NIFTY",
        "exchange": "NFO",
        "tradingsymbol": "...",
        "expiry": "2026-08-25",
        "strike": 25000,
        "option_type": "PE",
        "lot_size": 75,

        "ltp": 115.50,
        "bid": 115.20,
        "ask": 115.80,

        "volume": 850000,
        "open_interest": 1200000,

        "delta": -0.18,
        "iv": 14.8
    }
    """

    VALID_OPTION_TYPES = {
        "CE",
        "PE",
    }

    VALID_SIDES = {
        "BUY",
        "SELL",
    }

    VALID_EXCHANGES = {
        "NFO",
        "BFO",
    }

    VALID_PROFILES = {
        "CONSERVATIVE",
        "BALANCED",
        "CUSTOM",
    }

    # ========================================================
    # DEFAULT PROFILE SETTINGS
    # ========================================================

    DEFAULT_PROFILES = {
        "CONSERVATIVE": {
            "min_abs_delta": 0.10,
            "max_abs_delta": 0.16,
            "target_abs_delta": 0.13,

            "min_premium": 20.0,
            "max_premium": None,

            "min_open_interest": 10000,
            "min_volume": 5000,

            "max_spread_pct": 2.00,
        },

        "BALANCED": {
            "min_abs_delta": 0.15,
            "max_abs_delta": 0.22,
            "target_abs_delta": 0.18,

            "min_premium": 20.0,
            "max_premium": None,

            "min_open_interest": 10000,
            "min_volume": 5000,

            "max_spread_pct": 2.00,
        },
    }

    # ========================================================
    # CONSTRUCTOR
    # ========================================================

    def __init__(
        self,
        default_profile="BALANCED",
    ):

        default_profile = (
            self._normalize_text(
                default_profile
            )
        )

        if (
            default_profile
            not in self.VALID_PROFILES
        ):

            raise ValueError(
                "Invalid default contract-selection profile"
            )

        self.default_profile = (
            default_profile
        )

    # ========================================================
    # SAFE FLOAT
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

    # ========================================================
    # SAFE INTEGER
    # ========================================================

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

    # ========================================================
    # NORMALIZE TEXT
    # ========================================================

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
    # NORMALIZE EXPIRY
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

        # Try common ISO format first.
        try:

            parsed = datetime.strptime(
                text,
                "%Y-%m-%d",
            )

            return parsed.date().isoformat()

        except ValueError:
            pass

        # Zerodha/instrument data may sometimes be converted
        # to text in DD-MM-YYYY form.
        try:

            parsed = datetime.strptime(
                text,
                "%d-%m-%Y",
            )

            return parsed.date().isoformat()

        except ValueError:
            pass

        # Preserve unknown-but-present format so exact
        # comparison can still be attempted.
        return text

    # ========================================================
    # SPREAD CALCULATION
    # ========================================================

    def _calculate_spread(
        self,
        bid,
        ask,
    ):

        bid = self._safe_float(
            bid
        )

        ask = self._safe_float(
            ask
        )

        if (
            bid is None
            or ask is None
            or bid <= 0
            or ask <= 0
            or ask < bid
        ):

            return {
                "valid": False,
                "spread": None,
                "spread_pct": None,
            }

        spread = (
            ask - bid
        )

        midpoint = (
            bid + ask
        ) / 2.0

        if midpoint <= 0:

            return {
                "valid": False,
                "spread": None,
                "spread_pct": None,
            }

        spread_pct = (
            spread
            / midpoint
        ) * 100.0

        return {
            "valid": True,

            "spread":
                round(
                    spread,
                    4,
                ),

            "spread_pct":
                round(
                    spread_pct,
                    4,
                ),
        }

    # ========================================================
    # PROFILE CONFIGURATION
    # ========================================================

    def _build_profile(
        self,
        profile,
        custom_config=None,
    ):

        profile = (
            self._normalize_text(
                profile
            )
        )

        if not profile:

            profile = (
                self.default_profile
            )

        if (
            profile
            not in self.VALID_PROFILES
        ):

            raise ValueError(
                f"Invalid selection profile: {profile}"
            )

        if profile == "CUSTOM":

            if not isinstance(
                custom_config,
                dict,
            ):

                raise ValueError(
                    "CUSTOM profile requires custom_config"
                )

            config = dict(
                custom_config
            )

        else:

            config = dict(
                self.DEFAULT_PROFILES[
                    profile
                ]
            )

            # Allow dashboard/config overrides later.
            if isinstance(
                custom_config,
                dict,
            ):

                config.update(
                    custom_config
                )

        required = [
            "min_abs_delta",
            "max_abs_delta",
            "target_abs_delta",
            "min_premium",
            "min_open_interest",
            "min_volume",
            "max_spread_pct",
        ]

        missing = [
            key
            for key in required
            if key not in config
        ]

        if missing:

            raise ValueError(
                "Missing selector configuration: "
                + ", ".join(
                    missing
                )
            )

        min_delta = self._safe_float(
            config.get(
                "min_abs_delta"
            )
        )

        max_delta = self._safe_float(
            config.get(
                "max_abs_delta"
            )
        )

        target_delta = self._safe_float(
            config.get(
                "target_abs_delta"
            )
        )

        min_premium = self._safe_float(
            config.get(
                "min_premium"
            )
        )

        max_premium = self._safe_float(
            config.get(
                "max_premium"
            )
        )

        min_oi = self._safe_int(
            config.get(
                "min_open_interest"
            )
        )

        min_volume = self._safe_int(
            config.get(
                "min_volume"
            )
        )

        max_spread_pct = self._safe_float(
            config.get(
                "max_spread_pct"
            )
        )

        if (
            min_delta is None
            or max_delta is None
            or target_delta is None
        ):

            raise ValueError(
                "Delta configuration must be numeric"
            )

        if (
            min_delta < 0
            or max_delta <= 0
            or min_delta > max_delta
        ):

            raise ValueError(
                "Invalid delta range"
            )

        if not (
            min_delta
            <= target_delta
            <= max_delta
        ):

            raise ValueError(
                "Target delta must be inside "
                "configured delta range"
            )

        if (
            min_premium is None
            or min_premium < 0
        ):

            raise ValueError(
                "Invalid minimum premium"
            )

        if (
            max_premium is not None
            and max_premium <= 0
        ):

            raise ValueError(
                "Invalid maximum premium"
            )

        if (
            max_premium is not None
            and max_premium < min_premium
        ):

            raise ValueError(
                "Maximum premium cannot be below "
                "minimum premium"
            )

        if (
            min_oi is None
            or min_oi < 0
        ):

            raise ValueError(
                "Invalid minimum open interest"
            )

        if (
            min_volume is None
            or min_volume < 0
        ):

            raise ValueError(
                "Invalid minimum volume"
            )

        if (
            max_spread_pct is None
            or max_spread_pct < 0
        ):

            raise ValueError(
                "Invalid maximum spread percentage"
            )

        return {
            "profile":
                profile,

            "min_abs_delta":
                float(
                    min_delta
                ),

            "max_abs_delta":
                float(
                    max_delta
                ),

            "target_abs_delta":
                float(
                    target_delta
                ),

            "min_premium":
                float(
                    min_premium
                ),

            "max_premium":
                (
                    float(
                        max_premium
                    )
                    if max_premium
                    is not None
                    else None
                ),

            "min_open_interest":
                int(
                    min_oi
                ),

            "min_volume":
                int(
                    min_volume
                ),

            "max_spread_pct":
                float(
                    max_spread_pct
                ),
        }

    # ========================================================
    # NORMALIZE CANDIDATE
    # ========================================================

    def _normalize_candidate(
        self,
        raw,
    ):

        if not isinstance(
            raw,
            dict,
        ):

            return None

        spread_data = (
            self._calculate_spread(
                raw.get(
                    "bid"
                ),
                raw.get(
                    "ask"
                ),
            )
        )

        delta = self._safe_float(
            raw.get(
                "delta"
            )
        )

        return {
            "underlying":
                self._normalize_text(
                    raw.get(
                        "underlying"
                    )
                ),

            "exchange":
                self._normalize_text(
                    raw.get(
                        "exchange"
                    )
                ),

            "tradingsymbol":
                self._normalize_text(
                    raw.get(
                        "tradingsymbol"
                    )
                ),

            "expiry":
                self._normalize_expiry(
                    raw.get(
                        "expiry"
                    )
                ),

            "strike":
                self._safe_float(
                    raw.get(
                        "strike"
                    )
                ),

            "option_type":
                self._normalize_text(
                    raw.get(
                        "option_type"
                    )
                ),

            "lot_size":
                self._safe_int(
                    raw.get(
                        "lot_size"
                    )
                ),

            "ltp":
                self._safe_float(
                    raw.get(
                        "ltp"
                    )
                ),

            "bid":
                self._safe_float(
                    raw.get(
                        "bid"
                    )
                ),

            "ask":
                self._safe_float(
                    raw.get(
                        "ask"
                    )
                ),

            "volume":
                self._safe_int(
                    raw.get(
                        "volume"
                    ),
                    default=0,
                ),

            "open_interest":
                self._safe_int(
                    raw.get(
                        "open_interest"
                    ),
                    default=0,
                ),

            "delta":
                delta,

            "abs_delta":
                (
                    abs(
                        delta
                    )
                    if delta
                    is not None
                    else None
                ),

            "iv":
                self._safe_float(
                    raw.get(
                        "iv"
                    )
                ),

            "spread":
                spread_data[
                    "spread"
                ],

            "spread_pct":
                spread_data[
                    "spread_pct"
                ],

            "spread_valid":
                spread_data[
                    "valid"
                ],

            # Preserve optional broker/instrument metadata.
            "instrument_token":
                raw.get(
                    "instrument_token"
                ),

            "exchange_token":
                raw.get(
                    "exchange_token"
                ),

            "tick_size":
                self._safe_float(
                    raw.get(
                        "tick_size"
                    )
                ),

            "raw":
                dict(
                    raw
                ),
        }

    # ========================================================
    # STRUCTURAL VALIDATION
    # ========================================================

    def _structural_rejections(
        self,
        contract,
    ):

        reasons = []

        if not contract[
            "underlying"
        ]:

            reasons.append(
                "MISSING_UNDERLYING"
            )

        if (
            contract[
                "exchange"
            ]
            not in self.VALID_EXCHANGES
        ):

            reasons.append(
                "INVALID_EXCHANGE"
            )

        if not contract[
            "tradingsymbol"
        ]:

            reasons.append(
                "MISSING_TRADINGSYMBOL"
            )

        if not contract[
            "expiry"
        ]:

            reasons.append(
                "MISSING_EXPIRY"
            )

        if (
            contract[
                "strike"
            ]
            is None
            or contract[
                "strike"
            ]
            <= 0
        ):

            reasons.append(
                "INVALID_STRIKE"
            )

        if (
            contract[
                "option_type"
            ]
            not in self.VALID_OPTION_TYPES
        ):

            reasons.append(
                "INVALID_OPTION_TYPE"
            )

        if (
            contract[
                "lot_size"
            ]
            is None
            or contract[
                "lot_size"
            ]
            <= 0
        ):

            reasons.append(
                "INVALID_LOT_SIZE"
            )

        if (
            contract[
                "ltp"
            ]
            is None
            or contract[
                "ltp"
            ]
            <= 0
        ):

            reasons.append(
                "INVALID_LTP"
            )

        if (
            contract[
                "delta"
            ]
            is None
        ):

            reasons.append(
                "MISSING_DELTA"
            )

        return reasons

    # ========================================================
    # CONTRACT FILTER
    # ========================================================

    def _evaluate_candidate(
        self,
        contract,
        underlying,
        option_type,
        expiry,
        exchange,
        config,
    ):

        rejection_reasons = (
            self._structural_rejections(
                contract
            )
        )

        # ----------------------------------------------------
        # REQUEST MATCHING
        # ----------------------------------------------------

        if (
            contract[
                "underlying"
            ]
            != underlying
        ):

            rejection_reasons.append(
                "UNDERLYING_MISMATCH"
            )

        if (
            contract[
                "option_type"
            ]
            != option_type
        ):

            rejection_reasons.append(
                "OPTION_TYPE_MISMATCH"
            )

        if (
            expiry is not None
            and contract[
                "expiry"
            ]
            != expiry
        ):

            rejection_reasons.append(
                "EXPIRY_MISMATCH"
            )

        if (
            exchange is not None
            and contract[
                "exchange"
            ]
            != exchange
        ):

            rejection_reasons.append(
                "EXCHANGE_MISMATCH"
            )

        # ----------------------------------------------------
        # QUOTE / SPREAD SAFETY
        # ----------------------------------------------------

        if not contract[
            "spread_valid"
        ]:

            rejection_reasons.append(
                "INVALID_BID_ASK"
            )

        else:

            if (
                contract[
                    "spread_pct"
                ]
                > config[
                    "max_spread_pct"
                ]
            ):

                rejection_reasons.append(
                    "SPREAD_TOO_WIDE"
                )

        # ----------------------------------------------------
        # LIQUIDITY
        # ----------------------------------------------------

        if (
            contract[
                "open_interest"
            ]
            < config[
                "min_open_interest"
            ]
        ):

            rejection_reasons.append(
                "OPEN_INTEREST_TOO_LOW"
            )

        if (
            contract[
                "volume"
            ]
            < config[
                "min_volume"
            ]
        ):

            rejection_reasons.append(
                "VOLUME_TOO_LOW"
            )

        # ----------------------------------------------------
        # DELTA
        # ----------------------------------------------------

        abs_delta = (
            contract[
                "abs_delta"
            ]
        )

        if abs_delta is not None:

            if (
                abs_delta
                < config[
                    "min_abs_delta"
                ]
                or abs_delta
                > config[
                    "max_abs_delta"
                ]
            ):

                rejection_reasons.append(
                    "DELTA_OUTSIDE_RANGE"
                )

        # ----------------------------------------------------
        # PREMIUM
        # ----------------------------------------------------

        ltp = contract[
            "ltp"
        ]

        if ltp is not None:

            if (
                ltp
                < config[
                    "min_premium"
                ]
            ):

                rejection_reasons.append(
                    "PREMIUM_TOO_LOW"
                )

            maximum = (
                config[
                    "max_premium"
                ]
            )

            if (
                maximum is not None
                and ltp > maximum
            ):

                rejection_reasons.append(
                    "PREMIUM_TOO_HIGH"
                )

        eligible = (
            len(
                rejection_reasons
            )
            == 0
        )

        return {
            "eligible":
                eligible,

            "rejection_reasons":
                rejection_reasons,
        }

    # ========================================================
    # SCORE ELIGIBLE CONTRACT
    # ========================================================

    def _score_candidate(
        self,
        contract,
        config,
    ):
        """
        Scoring philosophy:

        Delta suitability is the primary strategy objective.

        Liquidity and spread are safety/quality factors.

        IMPORTANT:
        Setup confidence is intentionally NOT used here.
        Confidence controls risk allocation upstream.
        """

        target_delta = (
            config[
                "target_abs_delta"
            ]
        )

        abs_delta = (
            contract[
                "abs_delta"
            ]
        )

        delta_range = max(
            config[
                "max_abs_delta"
            ]
            - config[
                "min_abs_delta"
            ],
            0.01,
        )

        delta_distance = abs(
            abs_delta
            - target_delta
        )

        delta_score = max(
            0.0,
            100.0
            * (
                1.0
                - (
                    delta_distance
                    / delta_range
                )
            ),
        )

        # ----------------------------------------------------
        # SPREAD SCORE
        # ----------------------------------------------------

        max_spread = max(
            config[
                "max_spread_pct"
            ],
            0.01,
        )

        spread_score = max(
            0.0,
            100.0
            * (
                1.0
                - (
                    contract[
                        "spread_pct"
                    ]
                    / max_spread
                )
            ),
        )

        # ----------------------------------------------------
        # OI SCORE
        # ----------------------------------------------------

        minimum_oi = max(
            config[
                "min_open_interest"
            ],
            1,
        )

        oi_ratio = (
            contract[
                "open_interest"
            ]
            / minimum_oi
        )

        oi_score = min(
            100.0,
            oi_ratio
            * 25.0,
        )

        # ----------------------------------------------------
        # VOLUME SCORE
        # ----------------------------------------------------

        minimum_volume = max(
            config[
                "min_volume"
            ],
            1,
        )

        volume_ratio = (
            contract[
                "volume"
            ]
            / minimum_volume
        )

        volume_score = min(
            100.0,
            volume_ratio
            * 25.0,
        )

        # ----------------------------------------------------
        # WEIGHTED SCORE
        # ----------------------------------------------------

        total_score = (
            delta_score
            * 0.55
            + spread_score
            * 0.20
            + oi_score
            * 0.15
            + volume_score
            * 0.10
        )

        return {
            "total_score":
                round(
                    total_score,
                    4,
                ),

            "delta_score":
                round(
                    delta_score,
                    4,
                ),

            "spread_score":
                round(
                    spread_score,
                    4,
                ),

            "oi_score":
                round(
                    oi_score,
                    4,
                ),

            "volume_score":
                round(
                    volume_score,
                    4,
                ),

            "delta_distance":
                round(
                    delta_distance,
                    6,
                ),
        }

    # ========================================================
    # BLOCK RESULT
    # ========================================================

    def _blocked_result(
        self,
        reason,
        profile=None,
        requested_underlying=None,
        requested_option_type=None,
        requested_side=None,
        requested_expiry=None,
        requested_exchange=None,
        candidate_count=0,
        eligible_count=0,
        evaluations=None,
        validation_errors=None,
    ):

        return {
            "selection_permission":
                "BLOCK",

            "selection_allowed":
                False,

            "reason":
                reason,

            "validation_errors":
                list(
                    validation_errors
                    or []
                ),

            "profile":
                profile,

            "requested_underlying":
                requested_underlying,

            "requested_option_type":
                requested_option_type,

            "requested_side":
                requested_side,

            "requested_expiry":
                requested_expiry,

            "requested_exchange":
                requested_exchange,

            "candidate_count":
                candidate_count,

            "eligible_count":
                eligible_count,

            "selected_contract":
                None,

            "selected_score":
                None,

            "evaluations":
                list(
                    evaluations
                    or []
                ),

            "broker_order_allowed":
                False,
        }

    # ========================================================
    # SELECT CONTRACT
    # ========================================================

    def select_contract(
        self,
        contracts,
        underlying,
        option_type,
        side,
        expiry=None,
        exchange=None,
        profile=None,
        custom_config=None,
    ):
        """
        Select the best eligible option contract.

        Parameters
        ----------
        contracts:
            Iterable of normalized/raw contract dictionaries.

        underlying:
            Example: NIFTY or SENSEX.

        option_type:
            CE or PE.

        side:
            BUY or SELL.

            Side is retained for strategy traceability.
            It does NOT change risk authority.

        expiry:
            Exact desired expiry.
            If supplied, only that expiry is eligible.

        exchange:
            Optional exact exchange filter.

        profile:
            CONSERVATIVE / BALANCED / CUSTOM.

        custom_config:
            Profile overrides or full CUSTOM configuration.
        """

        # ====================================================
        # NORMALIZE REQUEST
        # ====================================================

        underlying = (
            self._normalize_text(
                underlying
            )
        )

        option_type = (
            self._normalize_text(
                option_type
            )
        )

        side = (
            self._normalize_text(
                side
            )
        )

        exchange = (
            self._normalize_text(
                exchange
            )
            if exchange is not None
            else None
        )

        expiry = (
            self._normalize_expiry(
                expiry
            )
            if expiry is not None
            else None
        )

        profile_name = (
            self._normalize_text(
                profile
            )
            if profile is not None
            else self.default_profile
        )

        # ====================================================
        # REQUEST VALIDATION
        # ====================================================

        errors = []

        if not underlying:

            errors.append(
                "UNDERLYING_REQUIRED"
            )

        if (
            option_type
            not in self.VALID_OPTION_TYPES
        ):

            errors.append(
                "INVALID_OPTION_TYPE"
            )

        if (
            side
            not in self.VALID_SIDES
        ):

            errors.append(
                "INVALID_SIDE"
            )

        if (
            exchange is not None
            and exchange
            not in self.VALID_EXCHANGES
        ):

            errors.append(
                "INVALID_EXCHANGE"
            )

        if (
            contracts is None
        ):

            errors.append(
                "CONTRACTS_REQUIRED"
            )

        if errors:

            return self._blocked_result(
                reason=(
                    "INVALID_SELECTION_REQUEST"
                ),
                profile=profile_name,
                requested_underlying=underlying,
                requested_option_type=option_type,
                requested_side=side,
                requested_expiry=expiry,
                requested_exchange=exchange,
                validation_errors=errors,
            )

        # ====================================================
        # BUILD PROFILE
        # ====================================================

        try:

            config = (
                self._build_profile(
                    profile=profile_name,
                    custom_config=custom_config,
                )
            )

        except Exception as error:

            return self._blocked_result(
                reason=(
                    "INVALID_PROFILE_CONFIGURATION"
                ),
                profile=profile_name,
                requested_underlying=underlying,
                requested_option_type=option_type,
                requested_side=side,
                requested_expiry=expiry,
                requested_exchange=exchange,
                validation_errors=[
                    str(error)
                ],
            )

        # ====================================================
        # NORMALIZE CONTRACT COLLECTION
        # ====================================================

        try:

            raw_contracts = list(
                contracts
            )

        except TypeError:

            return self._blocked_result(
                reason=(
                    "INVALID_CONTRACT_COLLECTION"
                ),
                profile=(
                    config[
                        "profile"
                    ]
                ),
                requested_underlying=underlying,
                requested_option_type=option_type,
                requested_side=side,
                requested_expiry=expiry,
                requested_exchange=exchange,
                validation_errors=[
                    "CONTRACTS_MUST_BE_ITERABLE"
                ],
            )

        if not raw_contracts:

            return self._blocked_result(
                reason=(
                    "NO_CONTRACTS_AVAILABLE"
                ),
                profile=(
                    config[
                        "profile"
                    ]
                ),
                requested_underlying=underlying,
                requested_option_type=option_type,
                requested_side=side,
                requested_expiry=expiry,
                requested_exchange=exchange,
                candidate_count=0,
            )

        # ====================================================
        # EVALUATE CANDIDATES
        # ====================================================

        evaluations = []
        eligible_contracts = []

        for index, raw in enumerate(
            raw_contracts
        ):

            contract = (
                self._normalize_candidate(
                    raw
                )
            )

            if contract is None:

                evaluations.append(
                    {
                        "index":
                            index,

                        "tradingsymbol":
                            None,

                        "eligible":
                            False,

                        "rejection_reasons": [
                            "INVALID_CONTRACT_OBJECT"
                        ],

                        "score":
                            None,
                    }
                )

                continue

            evaluation = (
                self._evaluate_candidate(
                    contract=contract,
                    underlying=underlying,
                    option_type=option_type,
                    expiry=expiry,
                    exchange=exchange,
                    config=config,
                )
            )

            score_data = None

            if evaluation[
                "eligible"
            ]:

                score_data = (
                    self._score_candidate(
                        contract,
                        config,
                    )
                )

                eligible_contracts.append(
                    {
                        "contract":
                            contract,

                        "score":
                            score_data,
                    }
                )

            evaluations.append(
                {
                    "index":
                        index,

                    "tradingsymbol":
                        contract[
                            "tradingsymbol"
                        ],

                    "strike":
                        contract[
                            "strike"
                        ],

                    "expiry":
                        contract[
                            "expiry"
                        ],

                    "option_type":
                        contract[
                            "option_type"
                        ],

                    "ltp":
                        contract[
                            "ltp"
                        ],

                    "delta":
                        contract[
                            "delta"
                        ],

                    "abs_delta":
                        contract[
                            "abs_delta"
                        ],

                    "spread_pct":
                        contract[
                            "spread_pct"
                        ],

                    "open_interest":
                        contract[
                            "open_interest"
                        ],

                    "volume":
                        contract[
                            "volume"
                        ],

                    "eligible":
                        evaluation[
                            "eligible"
                        ],

                    "rejection_reasons":
                        list(
                            evaluation[
                                "rejection_reasons"
                            ]
                        ),

                    "score":
                        (
                            score_data[
                                "total_score"
                            ]
                            if score_data
                            else None
                        ),

                    "score_breakdown":
                        (
                            dict(
                                score_data
                            )
                            if score_data
                            else None
                        ),
                }
            )

        # ====================================================
        # NOTHING ELIGIBLE
        # ====================================================

        if not eligible_contracts:

            return self._blocked_result(
                reason=(
                    "NO_ELIGIBLE_CONTRACT"
                ),
                profile=(
                    config[
                        "profile"
                    ]
                ),
                requested_underlying=underlying,
                requested_option_type=option_type,
                requested_side=side,
                requested_expiry=expiry,
                requested_exchange=exchange,
                candidate_count=len(
                    raw_contracts
                ),
                eligible_count=0,
                evaluations=evaluations,
            )

        # ====================================================
        # SORT BEST CONTRACT
        # ====================================================

        eligible_contracts.sort(
            key=lambda item: (
                -item[
                    "score"
                ][
                    "total_score"
                ],

                item[
                    "score"
                ][
                    "delta_distance"
                ],

                item[
                    "contract"
                ][
                    "spread_pct"
                ],

                -item[
                    "contract"
                ][
                    "open_interest"
                ],

                -item[
                    "contract"
                ][
                    "volume"
                ],

                item[
                    "contract"
                ][
                    "strike"
                ],
            )
        )

        winner = (
            eligible_contracts[0]
        )

        contract = (
            winner[
                "contract"
            ]
        )

        score = (
            winner[
                "score"
            ]
        )

        # ====================================================
        # BUILD TRADE-PLAN COMPATIBLE CONTRACT
        # ====================================================

        selected_contract = {
            # Fields directly required by TradePlanEngine.
            "underlying":
                contract[
                    "underlying"
                ],

            "exchange":
                contract[
                    "exchange"
                ],

            "tradingsymbol":
                contract[
                    "tradingsymbol"
                ],

            "expiry":
                contract[
                    "expiry"
                ],

            "strike":
                contract[
                    "strike"
                ],

            "option_type":
                contract[
                    "option_type"
                ],

            "lot_size":
                contract[
                    "lot_size"
                ],

            # Strategy/order traceability.
            "side":
                side,

            # Market information.
            "ltp":
                contract[
                    "ltp"
                ],

            "bid":
                contract[
                    "bid"
                ],

            "ask":
                contract[
                    "ask"
                ],

            "spread":
                contract[
                    "spread"
                ],

            "spread_pct":
                contract[
                    "spread_pct"
                ],

            "volume":
                contract[
                    "volume"
                ],

            "open_interest":
                contract[
                    "open_interest"
                ],

            "delta":
                contract[
                    "delta"
                ],

            "abs_delta":
                contract[
                    "abs_delta"
                ],

            "iv":
                contract[
                    "iv"
                ],

            # Broker/instrument metadata.
            "instrument_token":
                contract[
                    "instrument_token"
                ],

            "exchange_token":
                contract[
                    "exchange_token"
                ],

            "tick_size":
                contract[
                    "tick_size"
                ],

            # Selection audit.
            "selection_profile":
                config[
                    "profile"
                ],

            "selection_score":
                score[
                    "total_score"
                ],

            "score_breakdown":
                dict(
                    score
                ),
        }

        # ====================================================
        # FINAL SAFETY ASSERTIONS
        # ====================================================

        if (
            selected_contract[
                "option_type"
            ]
            != option_type
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: selector returned "
                "wrong option type"
            )

        if (
            selected_contract[
                "underlying"
            ]
            != underlying
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: selector returned "
                "wrong underlying"
            )

        if (
            expiry is not None
            and selected_contract[
                "expiry"
            ]
            != expiry
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: selector returned "
                "wrong expiry"
            )

        if (
            exchange is not None
            and selected_contract[
                "exchange"
            ]
            != exchange
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: selector returned "
                "wrong exchange"
            )

        if (
            selected_contract[
                "lot_size"
            ]
            <= 0
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: invalid selected "
                "contract lot size"
            )

        # ====================================================
        # SUCCESS
        # ====================================================

        return {
            "selection_permission":
                "ALLOW",

            "selection_allowed":
                True,

            "reason":
                "OPTION_CONTRACT_SELECTED",

            "validation_errors":
                [],

            "profile":
                config[
                    "profile"
                ],

            "profile_config":
                dict(
                    config
                ),

            "requested_underlying":
                underlying,

            "requested_option_type":
                option_type,

            "requested_side":
                side,

            "requested_expiry":
                expiry,

            "requested_exchange":
                exchange,

            "candidate_count":
                len(
                    raw_contracts
                ),

            "eligible_count":
                len(
                    eligible_contracts
                ),

            "selected_contract":
                selected_contract,

            "selected_score":
                score[
                    "total_score"
                ],

            "evaluations":
                evaluations,

            # This module never has execution authority.
            "broker_order_allowed":
                False,
        }

    # ========================================================
    # FRIENDLY ALIAS
    # ========================================================

    def select(
        self,
        **kwargs,
    ):

        return self.select_contract(
            **kwargs
        )