# ============================================================
# THETA AI TRADER — POSITION SIZING ENGINE
# ============================================================


class PositionSizingEngine:
    """
    Risk-based position sizing engine.

    Responsibilities:
    - Accept permission and risk budget from RiskManagementEngine
    - Calculate rupee risk per lot
    - Calculate lots allowed by stop-loss risk
    - Apply capital / margin constraints
    - Apply dashboard-configurable maximum-lot constraints
    - Apply dashboard-configurable expiry-day lot restrictions
    - Dynamically refresh configuration
    - Select the safest final quantity
    - Refuse sizing when upstream risk blocks trading

    This engine performs sizing only.

    It does NOT:
    - Fetch market data
    - Select strikes
    - Generate signals
    - Place orders
    - Modify positions
    """

    def __init__(
        self,
        config_manager=None,
        use_dynamic_config=True,
        default_max_lots=10,
        default_expiry_max_lots=5,
    ):
        self.config_manager = config_manager

        self.use_dynamic_config = bool(
            use_dynamic_config
        )

        self.default_max_lots = int(
            default_max_lots
        )

        self.default_expiry_max_lots = int(
            default_expiry_max_lots
        )

        if self.default_max_lots <= 0:
            raise ValueError(
                "default_max_lots must be greater than zero"
            )

        if self.default_expiry_max_lots <= 0:
            raise ValueError(
                "default_expiry_max_lots must be greater than zero"
            )

        # ----------------------------------------------------
        # AUTO-CONNECT CONFIG MANAGER
        # ----------------------------------------------------

        if (
            self.use_dynamic_config
            and self.config_manager is None
        ):
            try:
                from config_manager import ConfigManager

                self.config_manager = ConfigManager()

            except (
                ImportError,
                RuntimeError,
                OSError,
                ValueError,
            ):
                self.config_manager = None

    # --------------------------------------------------------
    # SAFE FLOAT
    # --------------------------------------------------------

    def _safe_float(
        self,
        value,
        default=0.0,
    ):
        try:
            return float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return float(
                default
            )

    # --------------------------------------------------------
    # SAFE INTEGER
    # --------------------------------------------------------

    def _safe_int(
        self,
        value,
        default=0,
    ):
        try:
            return int(
                value
            )

        except (
            TypeError,
            ValueError,
        ):
            return int(
                default
            )

    # --------------------------------------------------------
    # CONFIG VERSION
    # --------------------------------------------------------

    @property
    def config_version(self):
        """
        Return currently active configuration version.

        Returns None when dynamic configuration is
        unavailable.
        """

        if (
            not self.use_dynamic_config
            or self.config_manager is None
        ):
            return None

        try:
            return self.config_manager.get_setting(
                "system",
                "config_version",
            )

        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
        ):
            return None

    # --------------------------------------------------------
    # CONFIG LOOKUP
    # --------------------------------------------------------

    def _get_config_setting(
        self,
        key,
        default,
    ):
        """
        Read position-sizing configuration dynamically.

        IMPORTANT:

        Configuration is read every time sizing is
        calculated.

        This means future dashboard changes can affect
        subsequent sizing calculations without modifying
        PositionSizingEngine source code.
        """

        if (
            not self.use_dynamic_config
            or self.config_manager is None
        ):
            return default

        try:
            value = (
                self.config_manager
                .get_setting(
                    "position_sizing",
                    key,
                )
            )

            if value is None:
                return default

            return value

        except (
            KeyError,
            TypeError,
            ValueError,
            AttributeError,
        ):
            return default

    # --------------------------------------------------------
    # GET ACTIVE LIMITS
    # --------------------------------------------------------

    def get_active_limits(
        self,
        is_expiry_day=False,
    ):
        """
        Return currently active lot restrictions.

        Dashboard configuration:

        position_sizing.max_lots_per_trade

        position_sizing.expiry_max_lots_per_trade

        Expiry-day limit is always the safer/smaller of
        the normal maximum and expiry maximum.
        """

        max_lots = self._safe_int(
            self._get_config_setting(
                "max_lots_per_trade",
                self.default_max_lots,
            ),
            self.default_max_lots,
        )

        expiry_max_lots = self._safe_int(
            self._get_config_setting(
                "expiry_max_lots_per_trade",
                self.default_expiry_max_lots,
            ),
            self.default_expiry_max_lots,
        )

        if max_lots <= 0:
            max_lots = (
                self.default_max_lots
            )

        if expiry_max_lots <= 0:
            expiry_max_lots = (
                self.default_expiry_max_lots
            )

        if is_expiry_day:

            active_max_lots = min(
                max_lots,
                expiry_max_lots,
            )

        else:

            active_max_lots = (
                max_lots
            )

        return {
            "max_lots_per_trade": (
                max_lots
            ),

            "expiry_max_lots_per_trade": (
                expiry_max_lots
            ),

            # Backward-compatible alias.
            "max_expiry_lots_per_trade": (
                expiry_max_lots
            ),

            "active_max_lots": (
                active_max_lots
            ),

            "config_version": (
                self.config_version
            ),

            "dynamic_config": bool(
                self.use_dynamic_config
                and self.config_manager
                is not None
            ),
        }

    # --------------------------------------------------------
    # BLOCKED RESULT
    # --------------------------------------------------------

    def _blocked_result(
        self,
        reason,
        risk_permission="BLOCK",
        is_expiry_day=False,
    ):
        """
        Standard zero-quantity blocked result.
        """

        limits = self.get_active_limits(
            is_expiry_day=is_expiry_day
        )

        return {
            "sizing_permission": "BLOCK",

            "position_allowed": False,

            "reason": reason,

            "risk_permission": (
                risk_permission
            ),

            "is_expiry_day": bool(
                is_expiry_day
            ),

            "lot_size": 0,

            "final_lots": 0,

            "final_quantity": 0,

            "allowed_risk_rupees": 0.0,

            "stop_loss_per_unit": 0.0,

            "risk_per_lot": 0.0,

            "lots_by_risk": 0,

            "lots_by_margin": 0,

            "lots_by_config": (
                limits["active_max_lots"]
            ),

            "margin_per_lot": 0.0,

            "available_margin": 0.0,

            "estimated_margin_required": 0.0,

            "estimated_max_loss": 0.0,

            "max_lots_per_trade": (
                limits[
                    "max_lots_per_trade"
                ]
            ),

            "expiry_max_lots_per_trade": (
                limits[
                    "expiry_max_lots_per_trade"
                ]
            ),

            # Backward compatibility with existing tests.
            "max_expiry_lots_per_trade": (
                limits[
                    "expiry_max_lots_per_trade"
                ]
            ),

            "active_max_lots": (
                limits[
                    "active_max_lots"
                ]
            ),

            "config_version": (
                limits[
                    "config_version"
                ]
            ),

            "dynamic_config": (
                limits[
                    "dynamic_config"
                ]
            ),

            "limiting_factor": (
                "BLOCKED"
            ),
        }

    # --------------------------------------------------------
    # ANALYZE POSITION SIZE
    # --------------------------------------------------------

    def analyze(
        self,
        risk_analysis,
        lot_size,
        stop_loss_per_unit,
        margin_per_lot,
        available_margin,
        is_expiry_day=False,
    ):
        """
        Calculate the maximum safe position size.

        Position size is the minimum of:

            lots allowed by risk
            lots allowed by margin
            lots allowed by configuration

        No order is placed by this method.
        """

        # ----------------------------------------------------
        # INPUT VALIDATION
        # ----------------------------------------------------

        if not isinstance(
            risk_analysis,
            dict,
        ):
            return self._blocked_result(
                reason="INVALID_RISK_ANALYSIS",
                is_expiry_day=is_expiry_day,
            )

        risk_permission = str(
            risk_analysis.get(
                "risk_permission",
                "BLOCK",
            )
        ).upper()

        entry_allowed = bool(
            risk_analysis.get(
                "entry_allowed",
                False,
            )
        )

        allowed_risk_rupees = (
            self._safe_float(
                risk_analysis.get(
                    "allowed_risk_rupees",
                    0.0,
                )
            )
        )

        # ----------------------------------------------------
        # UPSTREAM RISK BLOCK
        # ----------------------------------------------------

        if (
            risk_permission == "BLOCK"
            or not entry_allowed
        ):

            result = self._blocked_result(
                reason="UPSTREAM_RISK_BLOCK",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

            result[
                "allowed_risk_rupees"
            ] = max(
                allowed_risk_rupees,
                0.0,
            )

            return result

        # ----------------------------------------------------
        # RISK BUDGET VALIDATION
        # ----------------------------------------------------

        if allowed_risk_rupees <= 0:

            return self._blocked_result(
                reason="NO_RISK_BUDGET",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        # ----------------------------------------------------
        # LOT SIZE VALIDATION
        # ----------------------------------------------------

        lot_size = self._safe_int(
            lot_size
        )

        if lot_size <= 0:

            return self._blocked_result(
                reason="INVALID_LOT_SIZE",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        # ----------------------------------------------------
        # STOP LOSS VALIDATION
        # ----------------------------------------------------

        stop_loss_per_unit = (
            self._safe_float(
                stop_loss_per_unit
            )
        )

        if stop_loss_per_unit <= 0:

            return self._blocked_result(
                reason="INVALID_STOP_LOSS",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        # ----------------------------------------------------
        # MARGIN VALIDATION
        # ----------------------------------------------------

        margin_per_lot = (
            self._safe_float(
                margin_per_lot
            )
        )

        available_margin = (
            self._safe_float(
                available_margin
            )
        )

        if margin_per_lot <= 0:

            return self._blocked_result(
                reason="INVALID_MARGIN_PER_LOT",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        if available_margin <= 0:

            return self._blocked_result(
                reason="NO_AVAILABLE_MARGIN",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        # ----------------------------------------------------
        # RISK PER LOT
        # ----------------------------------------------------

        risk_per_lot = (
            stop_loss_per_unit
            * lot_size
        )

        if risk_per_lot <= 0:

            return self._blocked_result(
                reason="INVALID_RISK_PER_LOT",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        # ----------------------------------------------------
        # LOTS ALLOWED BY RISK
        # ----------------------------------------------------

        lots_by_risk = int(
            allowed_risk_rupees
            // risk_per_lot
        )

        # ----------------------------------------------------
        # LOTS ALLOWED BY MARGIN
        # ----------------------------------------------------

        lots_by_margin = int(
            available_margin
            // margin_per_lot
        )

        # ----------------------------------------------------
        # DYNAMIC CONFIG LIMITS
        # ----------------------------------------------------

        limits = self.get_active_limits(
            is_expiry_day=is_expiry_day
        )

        lots_by_config = int(
            limits[
                "active_max_lots"
            ]
        )

        # ----------------------------------------------------
        # FINAL LOTS
        # ----------------------------------------------------

        final_lots = min(
            lots_by_risk,
            lots_by_margin,
            lots_by_config,
        )

        # ----------------------------------------------------
        # ZERO LOT / BLOCK CONDITIONS
        # ----------------------------------------------------

        if final_lots <= 0:

            if lots_by_risk <= 0:

                limiting_factor = (
                    "RISK_BUDGET"
                )

                reason = (
                    "RISK_BUDGET_TOO_SMALL_FOR_ONE_LOT"
                )

            elif lots_by_margin <= 0:

                limiting_factor = (
                    "AVAILABLE_MARGIN"
                )

                reason = (
                    "INSUFFICIENT_MARGIN_FOR_ONE_LOT"
                )

            else:

                limiting_factor = (
                    "CONFIG_LIMIT"
                )

                reason = (
                    "CONFIGURATION_BLOCKED_POSITION"
                )

            return {
                "sizing_permission": "BLOCK",

                "position_allowed": False,

                "reason": reason,

                "risk_permission": (
                    risk_permission
                ),

                "is_expiry_day": bool(
                    is_expiry_day
                ),

                "lot_size": lot_size,

                "final_lots": 0,

                "final_quantity": 0,

                "allowed_risk_rupees": (
                    allowed_risk_rupees
                ),

                "stop_loss_per_unit": (
                    stop_loss_per_unit
                ),

                "risk_per_lot": (
                    risk_per_lot
                ),

                "lots_by_risk": (
                    lots_by_risk
                ),

                "lots_by_margin": (
                    lots_by_margin
                ),

                "lots_by_config": (
                    lots_by_config
                ),

                "margin_per_lot": (
                    margin_per_lot
                ),

                "available_margin": (
                    available_margin
                ),

                "estimated_margin_required": 0.0,

                "estimated_max_loss": 0.0,

                "max_lots_per_trade": (
                    limits[
                        "max_lots_per_trade"
                    ]
                ),

                "expiry_max_lots_per_trade": (
                    limits[
                        "expiry_max_lots_per_trade"
                    ]
                ),

                "max_expiry_lots_per_trade": (
                    limits[
                        "expiry_max_lots_per_trade"
                    ]
                ),

                "active_max_lots": (
                    limits[
                        "active_max_lots"
                    ]
                ),

                "config_version": (
                    limits[
                        "config_version"
                    ]
                ),

                "dynamic_config": (
                    limits[
                        "dynamic_config"
                    ]
                ),

                "limiting_factor": (
                    limiting_factor
                ),
            }

        # ----------------------------------------------------
        # FINAL QUANTITY
        # ----------------------------------------------------

        final_quantity = (
            final_lots
            * lot_size
        )

        estimated_margin_required = (
            final_lots
            * margin_per_lot
        )

        estimated_max_loss = (
            final_lots
            * risk_per_lot
        )

        # ----------------------------------------------------
        # DETERMINE LIMITING FACTOR
        # ----------------------------------------------------

        minimum_limit = min(
            lots_by_risk,
            lots_by_margin,
            lots_by_config,
        )

        limiting_factors = []

        if (
            lots_by_risk
            == minimum_limit
        ):
            limiting_factors.append(
                "RISK_BUDGET"
            )

        if (
            lots_by_margin
            == minimum_limit
        ):
            limiting_factors.append(
                "AVAILABLE_MARGIN"
            )

        if (
            lots_by_config
            == minimum_limit
        ):

            if is_expiry_day:

                limiting_factors.append(
                    "EXPIRY_LOT_LIMIT"
                )

            else:

                limiting_factors.append(
                    "MAX_LOT_LIMIT"
                )

        limiting_factor = "+".join(
            limiting_factors
        )

        # ----------------------------------------------------
        # FINAL SAFETY CHECKS
        # ----------------------------------------------------

        if (
            estimated_max_loss
            > allowed_risk_rupees
        ):

            return self._blocked_result(
                reason="RISK_LIMIT_VIOLATION",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        if (
            estimated_margin_required
            > available_margin
        ):

            return self._blocked_result(
                reason="MARGIN_LIMIT_VIOLATION",
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        return {
            "sizing_permission": "ALLOW",

            "position_allowed": True,

            "reason": (
                "POSITION_SIZE_CALCULATED"
            ),

            "risk_permission": (
                risk_permission
            ),

            "is_expiry_day": bool(
                is_expiry_day
            ),

            "lot_size": (
                lot_size
            ),

            "final_lots": (
                final_lots
            ),

            "final_quantity": (
                final_quantity
            ),

            "allowed_risk_rupees": (
                allowed_risk_rupees
            ),

            "stop_loss_per_unit": (
                stop_loss_per_unit
            ),

            "risk_per_lot": (
                risk_per_lot
            ),

            "lots_by_risk": (
                lots_by_risk
            ),

            "lots_by_margin": (
                lots_by_margin
            ),

            "lots_by_config": (
                lots_by_config
            ),

            "margin_per_lot": (
                margin_per_lot
            ),

            "available_margin": (
                available_margin
            ),

            "estimated_margin_required": (
                estimated_margin_required
            ),

            "estimated_max_loss": (
                estimated_max_loss
            ),

            "max_lots_per_trade": (
                limits[
                    "max_lots_per_trade"
                ]
            ),

            "expiry_max_lots_per_trade": (
                limits[
                    "expiry_max_lots_per_trade"
                ]
            ),

            # Keep old output key so existing code/tests
            # do not unexpectedly break.
            "max_expiry_lots_per_trade": (
                limits[
                    "expiry_max_lots_per_trade"
                ]
            ),

            "active_max_lots": (
                limits[
                    "active_max_lots"
                ]
            ),

            "config_version": (
                limits[
                    "config_version"
                ]
            ),

            "dynamic_config": (
                limits[
                    "dynamic_config"
                ]
            ),

            "limiting_factor": (
                limiting_factor
            ),
        }

    # --------------------------------------------------------
    # ALIAS FOR FUTURE PIPELINE
    # --------------------------------------------------------

    def calculate(
        self,
        risk_analysis,
        lot_size,
        stop_loss_per_unit,
        margin_per_lot,
        available_margin,
        is_expiry_day=False,
    ):
        """
        Alias for analyze().
        """

        return self.analyze(
            risk_analysis=risk_analysis,
            lot_size=lot_size,
            stop_loss_per_unit=stop_loss_per_unit,
            margin_per_lot=margin_per_lot,
            available_margin=available_margin,
            is_expiry_day=is_expiry_day,
        )