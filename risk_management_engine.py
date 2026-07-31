# ============================================================
# THETA AI TRADER — RISK MANAGEMENT ENGINE
# ============================================================

from config_manager import ConfigManager


class RiskManagementEngine:
    """
    Central capital-protection and trade-risk engine.

    Configuration architecture:

        Dashboard
            ↓
        ConfigManager
            ↓
        RiskManagementEngine
            ↓
        Risk Decision

    Risk settings can therefore be changed without modifying
    this Python file.

    Responsibilities:
    - Load configurable risk limits
    - Validate risk configuration
    - Refresh configuration when required
    - Validate account capital
    - Enforce per-trade risk limits
    - Enforce daily loss limits
    - Enforce account drawdown limits
    - Protect against consecutive losses
    - Limit simultaneous positions
    - Reduce risk on expiry days
    - Reduce risk during CAUTION conditions
    - Block unstable / spike conditions
    - Respect signal-engine trade permission
    - Support manual emergency kill switch
    - Calculate final risk multiplier
    - Calculate allowed rupee risk

    IMPORTANT:
    This engine performs risk analysis only.

    It does NOT:
    - Select strikes
    - Select strategies
    - Calculate broker order quantity
    - Place orders
    - Modify orders
    - Exit positions
    """

    # --------------------------------------------------------
    # SAFE FALLBACK DEFAULTS
    # --------------------------------------------------------

    DEFAULTS = {
        "max_risk_per_trade_pct": 1.0,
        "max_daily_loss_pct": 3.0,
        "max_account_drawdown_pct": 10.0,
        "max_consecutive_losses": 3,
        "max_open_positions": 3,
        "caution_risk_multiplier": 0.50,
        "expiry_risk_multiplier": 0.50,
        "medium_confidence_multiplier": 0.75,
        "minimum_risk_multiplier": 0.25,
    }

    # --------------------------------------------------------
    # INITIALIZATION
    # --------------------------------------------------------

    def __init__(
        self,
        config_manager=None,
        use_dynamic_config=True,
        max_risk_per_trade_pct=None,
        max_daily_loss_pct=None,
        max_account_drawdown_pct=None,
        max_consecutive_losses=None,
        max_open_positions=None,
        caution_risk_multiplier=None,
        expiry_risk_multiplier=None,
        medium_confidence_multiplier=None,
        minimum_risk_multiplier=None,
    ):

        self.config_manager = (
            config_manager
            or ConfigManager()
        )

        self.use_dynamic_config = bool(
            use_dynamic_config
        )

        # Explicit constructor values are retained as
        # overrides/fallbacks. This keeps the engine easy to
        # unit-test without depending on dashboard state.
        self._manual_overrides = {
            "max_risk_per_trade_pct":
                max_risk_per_trade_pct,

            "max_daily_loss_pct":
                max_daily_loss_pct,

            "max_account_drawdown_pct":
                max_account_drawdown_pct,

            "max_consecutive_losses":
                max_consecutive_losses,

            "max_open_positions":
                max_open_positions,

            "caution_risk_multiplier":
                caution_risk_multiplier,

            "expiry_risk_multiplier":
                expiry_risk_multiplier,

            "medium_confidence_multiplier":
                medium_confidence_multiplier,

            "minimum_risk_multiplier":
                minimum_risk_multiplier,
        }

        # Manual emergency protection must remain runtime
        # state and must NOT disappear during config refresh.
        self.kill_switch_active = False
        self.kill_switch_reason = None

        self.config_version = None

        self.refresh_config()

    # --------------------------------------------------------
    # SAFE FLOAT
    # --------------------------------------------------------

    def _safe_float(
        self,
        value,
        default=0.0,
    ):
        """
        Safely convert a value to float.
        """

        try:

            if value is None:
                return float(
                    default
                )

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
        """
        Safely convert a value to integer.
        """

        try:

            if value is None:
                return int(
                    default
                )

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
    # READ CONFIG VALUE
    # --------------------------------------------------------

    def _read_config(
        self,
        key,
        default,
    ):
        """
        Read one risk value from ConfigManager.

        Priority:

        1. Explicit constructor override
        2. ConfigManager value
        3. Safe engine default

        This allows tests or special instances to explicitly
        override dashboard configuration when necessary.
        """

        override = (
            self._manual_overrides.get(
                key
            )
        )

        if override is not None:
            return override

        if not self.use_dynamic_config:
            return default

        try:

            value = (
                self.config_manager
                .get_setting(
                    "risk",
                    key,
                )
            )

            if value is None:
                return default

            return value

        except Exception:
            # Risk engine must never crash simply because the
            # configuration source temporarily cannot be read.
            return default

    # --------------------------------------------------------
    # REFRESH CONFIGURATION
    # --------------------------------------------------------

    def refresh_config(self):
        """
        Reload risk configuration.

        This method is intentionally public.

        Future dashboard/API code can update ConfigManager,
        then either:

            risk_engine.refresh_config()

        or simply call analyze(), which automatically refreshes
        configuration when dynamic configuration is enabled.
        """

        self.max_risk_per_trade_pct = (
            self._safe_float(
                self._read_config(
                    "max_risk_per_trade_pct",
                    self.DEFAULTS[
                        "max_risk_per_trade_pct"
                    ],
                ),
                self.DEFAULTS[
                    "max_risk_per_trade_pct"
                ],
            )
        )

        self.max_daily_loss_pct = (
            self._safe_float(
                self._read_config(
                    "max_daily_loss_pct",
                    self.DEFAULTS[
                        "max_daily_loss_pct"
                    ],
                ),
                self.DEFAULTS[
                    "max_daily_loss_pct"
                ],
            )
        )

        self.max_account_drawdown_pct = (
            self._safe_float(
                self._read_config(
                    "max_account_drawdown_pct",
                    self.DEFAULTS[
                        "max_account_drawdown_pct"
                    ],
                ),
                self.DEFAULTS[
                    "max_account_drawdown_pct"
                ],
            )
        )

        self.max_consecutive_losses = (
            self._safe_int(
                self._read_config(
                    "max_consecutive_losses",
                    self.DEFAULTS[
                        "max_consecutive_losses"
                    ],
                ),
                self.DEFAULTS[
                    "max_consecutive_losses"
                ],
            )
        )

        self.max_open_positions = (
            self._safe_int(
                self._read_config(
                    "max_open_positions",
                    self.DEFAULTS[
                        "max_open_positions"
                    ],
                ),
                self.DEFAULTS[
                    "max_open_positions"
                ],
            )
        )

        self.caution_risk_multiplier = (
            self._safe_float(
                self._read_config(
                    "caution_risk_multiplier",
                    self.DEFAULTS[
                        "caution_risk_multiplier"
                    ],
                ),
                self.DEFAULTS[
                    "caution_risk_multiplier"
                ],
            )
        )

        self.expiry_risk_multiplier = (
            self._safe_float(
                self._read_config(
                    "expiry_risk_multiplier",
                    self.DEFAULTS[
                        "expiry_risk_multiplier"
                    ],
                ),
                self.DEFAULTS[
                    "expiry_risk_multiplier"
                ],
            )
        )

        self.medium_confidence_multiplier = (
            self._safe_float(
                self._read_config(
                    "medium_confidence_multiplier",
                    self.DEFAULTS[
                        "medium_confidence_multiplier"
                    ],
                ),
                self.DEFAULTS[
                    "medium_confidence_multiplier"
                ],
            )
        )

        self.minimum_risk_multiplier = (
            self._safe_float(
                self._read_config(
                    "minimum_risk_multiplier",
                    self.DEFAULTS[
                        "minimum_risk_multiplier"
                    ],
                ),
                self.DEFAULTS[
                    "minimum_risk_multiplier"
                ],
            )
        )

        try:

            self.config_version = (
                self.config_manager
                .get_setting(
                    "system",
                    "config_version",
                )
            )

        except Exception:

            self.config_version = None

        self._validate_configuration()

        return self.get_risk_config()

    # --------------------------------------------------------
    # CONFIGURATION VALIDATION
    # --------------------------------------------------------

    def _validate_configuration(self):
        """
        Validate active engine configuration.

        Invalid risk settings are rejected rather than silently
        weakening the protection layer.
        """

        if self.max_risk_per_trade_pct <= 0:
            raise ValueError(
                "max_risk_per_trade_pct must be "
                "greater than zero"
            )

        if self.max_daily_loss_pct <= 0:
            raise ValueError(
                "max_daily_loss_pct must be "
                "greater than zero"
            )

        if self.max_account_drawdown_pct <= 0:
            raise ValueError(
                "max_account_drawdown_pct must be "
                "greater than zero"
            )

        if self.max_consecutive_losses <= 0:
            raise ValueError(
                "max_consecutive_losses must be "
                "greater than zero"
            )

        if self.max_open_positions <= 0:
            raise ValueError(
                "max_open_positions must be "
                "greater than zero"
            )

        multipliers = (
            self.caution_risk_multiplier,
            self.expiry_risk_multiplier,
            self.medium_confidence_multiplier,
            self.minimum_risk_multiplier,
        )

        for multiplier in multipliers:

            if (
                multiplier <= 0
                or multiplier > 1
            ):
                raise ValueError(
                    "Risk multipliers must be "
                    "greater than 0 and <= 1"
                )

    # --------------------------------------------------------
    # GET ACTIVE RISK CONFIG
    # --------------------------------------------------------

    def get_risk_config(self):
        """
        Return the currently active risk configuration.

        Useful for:
        - Dashboard display
        - Diagnostics
        - Logging
        - Audit trail
        """

        return {
            "config_version":
                self.config_version,

            "dynamic_config_enabled":
                self.use_dynamic_config,

            "max_risk_per_trade_pct":
                self.max_risk_per_trade_pct,

            "max_daily_loss_pct":
                self.max_daily_loss_pct,

            "max_account_drawdown_pct":
                self.max_account_drawdown_pct,

            "max_consecutive_losses":
                self.max_consecutive_losses,

            "max_open_positions":
                self.max_open_positions,

            "caution_risk_multiplier":
                self.caution_risk_multiplier,

            "expiry_risk_multiplier":
                self.expiry_risk_multiplier,

            "medium_confidence_multiplier":
                self.medium_confidence_multiplier,

            "minimum_risk_multiplier":
                self.minimum_risk_multiplier,
        }

    # --------------------------------------------------------
    # KILL SWITCH
    # --------------------------------------------------------

    def activate_kill_switch(
        self,
        reason="MANUAL_KILL_SWITCH",
    ):
        """
        Immediately disable new trading permission.

        This does not place or cancel orders.
        """

        self.kill_switch_active = True

        self.kill_switch_reason = str(
            reason
        )

        return {
            "kill_switch_active": True,
            "reason": self.kill_switch_reason,
        }

    # --------------------------------------------------------
    # RESET KILL SWITCH
    # --------------------------------------------------------

    def reset_kill_switch(self):
        """
        Reset manual kill switch.
        """

        self.kill_switch_active = False

        self.kill_switch_reason = None

        return {
            "kill_switch_active": False,
            "reason": None,
        }

    # --------------------------------------------------------
    # CALCULATE PERCENTAGE
    # --------------------------------------------------------

    def _percentage(
        self,
        value,
        base,
    ):
        """
        Calculate absolute percentage of base.
        """

        value = abs(
            self._safe_float(
                value
            )
        )

        base = self._safe_float(
            base
        )

        if base <= 0:
            return 0.0

        return (
            value
            / base
        ) * 100.0

    # --------------------------------------------------------
    # DETERMINE DAILY LOSS
    # --------------------------------------------------------

    def _calculate_daily_loss_pct(
        self,
        daily_pnl,
        capital,
    ):
        """
        Convert negative daily P&L into loss percentage.
        """

        daily_pnl = self._safe_float(
            daily_pnl
        )

        capital = self._safe_float(
            capital
        )

        if (
            capital <= 0
            or daily_pnl >= 0
        ):
            return 0.0

        return (
            abs(
                daily_pnl
            )
            / capital
        ) * 100.0

    # --------------------------------------------------------
    # ACCOUNT DRAWDOWN
    # --------------------------------------------------------

    def _calculate_drawdown_pct(
        self,
        current_equity,
        peak_equity,
    ):
        """
        Calculate account drawdown from peak equity.
        """

        current_equity = self._safe_float(
            current_equity
        )

        peak_equity = self._safe_float(
            peak_equity
        )

        if (
            peak_equity <= 0
            or current_equity >= peak_equity
        ):
            return 0.0

        return (
            (
                peak_equity
                - current_equity
            )
            / peak_equity
        ) * 100.0

    # --------------------------------------------------------
    # MAIN RISK ANALYSIS
    # --------------------------------------------------------

    def analyze(
        self,
        capital,
        signal_analysis=None,
        account_state=None,
        volatility=None,
        session=None,
        is_expiry_day=False,
    ):
        """
        Evaluate whether a new trade is allowed.

        Dynamic configuration is refreshed before every risk
        decision when enabled. Therefore a dashboard change can
        affect subsequent decisions without restarting the
        engine.
        """

        # ----------------------------------------------------
        # REFRESH DASHBOARD CONFIG
        # ----------------------------------------------------

        if self.use_dynamic_config:
            self.refresh_config()

        signal_analysis = (
            signal_analysis
            or {}
        )

        account_state = (
            account_state
            or {}
        )

        volatility = (
            volatility
            or {}
        )

        session = (
            session
            or {}
        )

        capital = self._safe_float(
            capital
        )

        hard_blocks = []
        risk_reductions = []
        warnings = []

        risk_multiplier = 1.0

        # ----------------------------------------------------
        # CAPITAL VALIDATION
        # ----------------------------------------------------

        if capital <= 0:

            hard_blocks.append(
                "INVALID_CAPITAL"
            )

        # ----------------------------------------------------
        # SIGNAL ENGINE STATE
        # ----------------------------------------------------

        decision = str(
            signal_analysis.get(
                "decision",
                "WAIT",
            )
        ).upper()

        setup_valid = bool(
            signal_analysis.get(
                "setup_valid",
                False,
            )
        )

        signal_direction = str(
            signal_analysis.get(
                "direction",
                "NONE",
            )
        ).upper()

        signal_confidence = str(
            signal_analysis.get(
                "confidence",
                "LOW",
            )
        ).upper()

        trade_permission = str(
            signal_analysis.get(
                "trade_permission",
                "BLOCK",
            )
        ).upper()

        # ----------------------------------------------------
        # SIGNAL VALIDATION
        # ----------------------------------------------------

        if decision == "NO_TRADE":

            hard_blocks.append(
                "SIGNAL_NO_TRADE"
            )

        elif decision == "WAIT":

            hard_blocks.append(
                "SIGNAL_WAIT"
            )

        if not setup_valid:

            hard_blocks.append(
                "INVALID_SIGNAL_SETUP"
            )

        if trade_permission == "BLOCK":

            hard_blocks.append(
                "SIGNAL_PERMISSION_BLOCK"
            )

        # ----------------------------------------------------
        # SIGNAL CONFLICT
        # ----------------------------------------------------

        signal_conflict = bool(
            signal_analysis.get(
                "signal_conflict",
                False,
            )
        )

        if signal_conflict:

            hard_blocks.append(
                "SIGNAL_CONFLICT"
            )

        # ----------------------------------------------------
        # CAUTION PERMISSION
        # ----------------------------------------------------

        if trade_permission == "CAUTION":

            risk_multiplier *= (
                self.caution_risk_multiplier
            )

            risk_reductions.append(
                "CAUTION_PERMISSION"
            )

        # ----------------------------------------------------
        # CONFIDENCE CONTROL
        # ----------------------------------------------------

        if signal_confidence == "LOW":

            hard_blocks.append(
                "LOW_SIGNAL_CONFIDENCE"
            )

        elif signal_confidence == "MEDIUM":

            risk_multiplier *= (
                self.medium_confidence_multiplier
            )

            risk_reductions.append(
                "MEDIUM_SIGNAL_CONFIDENCE"
            )

        # ----------------------------------------------------
        # MANUAL KILL SWITCH
        # ----------------------------------------------------

        if self.kill_switch_active:

            hard_blocks.append(
                "KILL_SWITCH_ACTIVE"
            )

            if self.kill_switch_reason:

                warnings.append(
                    self.kill_switch_reason
                )

        # ----------------------------------------------------
        # SESSION SAFETY
        # ----------------------------------------------------

        if session:

            market_open = bool(
                session.get(
                    "market_open",
                    True,
                )
            )

            new_entries_allowed = bool(
                session.get(
                    "new_entries_allowed",
                    True,
                )
            )

            if not market_open:

                hard_blocks.append(
                    "MARKET_CLOSED"
                )

            if not new_entries_allowed:

                hard_blocks.append(
                    "SESSION_ENTRY_BLOCK"
                )

        # ----------------------------------------------------
        # VOLATILITY / SPIKE SAFETY
        # ----------------------------------------------------

        spike_detected = bool(
            volatility.get(
                "spike_detected",
                False,
            )
        )

        abnormal_candle = bool(
            volatility.get(
                "abnormal_candle",
                False,
            )
        )

        rapid_move = bool(
            volatility.get(
                "rapid_move",
                False,
            )
        )

        volatility_state = str(
            volatility.get(
                "volatility_state",
                "NORMAL",
            )
        ).upper()

        if spike_detected:

            hard_blocks.append(
                "ACTIVE_PRICE_SPIKE"
            )

        if rapid_move:

            hard_blocks.append(
                "RAPID_MOVE"
            )

        if abnormal_candle:

            hard_blocks.append(
                "ABNORMAL_CANDLE"
            )

        if volatility_state in (
            "EXTREME",
            "UNSTABLE",
        ):

            hard_blocks.append(
                "EXTREME_VOLATILITY"
            )

        # ----------------------------------------------------
        # EXPIRY-DAY RISK REDUCTION
        # ----------------------------------------------------

        is_expiry_day = bool(
            is_expiry_day
        )

        if is_expiry_day:

            risk_multiplier *= (
                self.expiry_risk_multiplier
            )

            risk_reductions.append(
                "EXPIRY_DAY_RISK_REDUCTION"
            )

        # ----------------------------------------------------
        # ACCOUNT STATE
        # ----------------------------------------------------

        daily_pnl = self._safe_float(
            account_state.get(
                "daily_pnl",
                0.0,
            )
        )

        current_equity = self._safe_float(
            account_state.get(
                "current_equity",
                capital,
            ),
            default=capital,
        )

        peak_equity = self._safe_float(
            account_state.get(
                "peak_equity",
                current_equity,
            ),
            default=current_equity,
        )

        consecutive_losses = self._safe_int(
            account_state.get(
                "consecutive_losses",
                0,
            )
        )

        open_positions = self._safe_int(
            account_state.get(
                "open_positions",
                0,
            )
        )

        # ----------------------------------------------------
        # DAILY LOSS PROTECTION
        # ----------------------------------------------------

        daily_loss_pct = (
            self._calculate_daily_loss_pct(
                daily_pnl=daily_pnl,
                capital=capital,
            )
        )

        if (
            daily_loss_pct
            >= self.max_daily_loss_pct
        ):

            hard_blocks.append(
                "DAILY_LOSS_LIMIT_REACHED"
            )

        # ----------------------------------------------------
        # ACCOUNT DRAWDOWN PROTECTION
        # ----------------------------------------------------

        account_drawdown_pct = (
            self._calculate_drawdown_pct(
                current_equity=current_equity,
                peak_equity=peak_equity,
            )
        )

        if (
            account_drawdown_pct
            >= self.max_account_drawdown_pct
        ):

            hard_blocks.append(
                "ACCOUNT_DRAWDOWN_LIMIT_REACHED"
            )

        # ----------------------------------------------------
        # CONSECUTIVE LOSS PROTECTION
        # ----------------------------------------------------

        if (
            consecutive_losses
            >= self.max_consecutive_losses
        ):

            hard_blocks.append(
                "CONSECUTIVE_LOSS_LIMIT_REACHED"
            )

        # ----------------------------------------------------
        # MAX OPEN POSITION PROTECTION
        # ----------------------------------------------------

        if (
            open_positions
            >= self.max_open_positions
        ):

            hard_blocks.append(
                "MAX_OPEN_POSITIONS_REACHED"
            )

        # ----------------------------------------------------
        # NORMALIZE MULTIPLIER
        # ----------------------------------------------------

        risk_multiplier = max(
            0.0,
            min(
                risk_multiplier,
                1.0,
            ),
        )

        # ----------------------------------------------------
        # BASE RISK PER TRADE
        # ----------------------------------------------------

        base_allowed_risk = 0.0

        if capital > 0:

            base_allowed_risk = (
                capital
                * (
                    self.max_risk_per_trade_pct
                    / 100.0
                )
            )

        # ----------------------------------------------------
        # HARD BLOCK OVERRIDES EVERYTHING
        # ----------------------------------------------------

        if hard_blocks:

            final_risk_multiplier = 0.0

            allowed_risk_rupees = 0.0

            risk_permission = "BLOCK"

            entry_allowed = False

        else:

            if (
                risk_multiplier
                < self.minimum_risk_multiplier
            ):

                risk_multiplier = (
                    self.minimum_risk_multiplier
                )

            final_risk_multiplier = (
                risk_multiplier
            )

            allowed_risk_rupees = (
                base_allowed_risk
                * final_risk_multiplier
            )

            entry_allowed = True

            if final_risk_multiplier < 1.0:

                risk_permission = (
                    "REDUCED_RISK"
                )

            else:

                risk_permission = (
                    "ALLOW"
                )

        # ----------------------------------------------------
        # ALLOWED RISK PERCENTAGE
        # ----------------------------------------------------

        allowed_risk_pct = (
            self.max_risk_per_trade_pct
            * final_risk_multiplier
        )

        # ----------------------------------------------------
        # RISK STATE
        # ----------------------------------------------------

        if risk_permission == "BLOCK":

            risk_state = "BLOCKED"

        elif final_risk_multiplier < 1.0:

            risk_state = "REDUCED"

        else:

            risk_state = "NORMAL"

        # ----------------------------------------------------
        # RETURN
        # ----------------------------------------------------

        return {
            "risk_permission":
                risk_permission,

            "entry_allowed":
                entry_allowed,

            "risk_state":
                risk_state,

            "capital":
                capital,

            # Configuration audit
            "config_version":
                self.config_version,

            "dynamic_config_enabled":
                self.use_dynamic_config,

            "max_risk_per_trade_pct":
                self.max_risk_per_trade_pct,

            "max_daily_loss_pct":
                self.max_daily_loss_pct,

            "max_account_drawdown_pct":
                self.max_account_drawdown_pct,

            "max_consecutive_losses":
                self.max_consecutive_losses,

            "max_open_positions":
                self.max_open_positions,

            # Signal
            "signal_decision":
                decision,

            "signal_direction":
                signal_direction,

            "signal_confidence":
                signal_confidence,

            "signal_trade_permission":
                trade_permission,

            "signal_conflict":
                signal_conflict,

            # Risk sizing
            "base_risk_pct":
                self.max_risk_per_trade_pct,

            "base_allowed_risk_rupees":
                base_allowed_risk,

            "risk_multiplier":
                final_risk_multiplier,

            "allowed_risk_pct":
                allowed_risk_pct,

            "allowed_risk_rupees":
                allowed_risk_rupees,

            # Account diagnostics
            "daily_pnl":
                daily_pnl,

            "daily_loss_pct":
                daily_loss_pct,

            "current_equity":
                current_equity,

            "peak_equity":
                peak_equity,

            "account_drawdown_pct":
                account_drawdown_pct,

            "consecutive_losses":
                consecutive_losses,

            "open_positions":
                open_positions,

            # Market risk
            "is_expiry_day":
                is_expiry_day,

            "spike_detected":
                spike_detected,

            "rapid_move":
                rapid_move,

            "abnormal_candle":
                abnormal_candle,

            "volatility_state":
                volatility_state,

            # Protection diagnostics
            "hard_blocks": list(
                dict.fromkeys(
                    hard_blocks
                )
            ),

            "risk_reductions": list(
                dict.fromkeys(
                    risk_reductions
                )
            ),

            "warnings": list(
                dict.fromkeys(
                    warnings
                )
            ),

            "kill_switch_active":
                self.kill_switch_active,

            "kill_switch_reason":
                self.kill_switch_reason,
        }