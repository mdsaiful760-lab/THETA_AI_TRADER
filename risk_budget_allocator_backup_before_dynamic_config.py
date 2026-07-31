# ============================================================
# THETA AI TRADER — RISK BUDGET ALLOCATOR
# ============================================================

from typing import Any, Dict, Optional


class RiskBudgetAllocator:
    """
    Intelligent daily risk-budget allocation engine.

    Responsibilities
    ----------------
    - Receive the user's daily risk budget in rupees.
    - Track the remaining daily risk available for NEW trades.
    - Support FIXED and INTELLIGENT allocation modes.
    - Respect maximum trade attempts when configured.
    - Adjust allocation according to setup-confidence score.
    - Prevent any allocation from exceeding remaining risk.
    - Apply a maximum-single-trade share of daily risk.
    - Refuse allocations when upstream safety blocks trading.

    IMPORTANT
    ---------
    This engine allocates RISK BUDGET.

    It does NOT:
    - calculate lots
    - place orders
    - calculate broker margin
    - generate trading signals
    - determine stop-loss prices
    - decide whether a market setup exists

    Final quantity belongs to PositionSizingEngine.

    HARD PRINCIPLE
    --------------
    Confidence may influence HOW the available risk is deployed.

    Confidence may NEVER create additional risk beyond the
    user's configured daily risk boundary.
    """

    MODE_FIXED = "FIXED"
    MODE_INTELLIGENT = "INTELLIGENT"

    # --------------------------------------------------------
    # INITIALIZATION
    # --------------------------------------------------------

    def __init__(
        self,
        allocation_mode: str = "FIXED",
        max_trades_per_day: int = 3,
        minimum_setup_score: float = 60.0,
        confidence_scaling_enabled: bool = True,
        max_single_trade_daily_risk_pct: float = 40.0,
        intelligent_reference_trades: int = 3,
    ):

        self.allocation_mode = self._normalize_mode(
            allocation_mode
        )

        self.max_trades_per_day = self._validate_positive_int(
            max_trades_per_day,
            "max_trades_per_day",
        )

        self.minimum_setup_score = self._validate_percentage(
            minimum_setup_score,
            "minimum_setup_score",
        )

        self.confidence_scaling_enabled = bool(
            confidence_scaling_enabled
        )

        self.max_single_trade_daily_risk_pct = (
            self._validate_percentage(
                max_single_trade_daily_risk_pct,
                "max_single_trade_daily_risk_pct",
                minimum=1.0,
            )
        )

        self.intelligent_reference_trades = (
            self._validate_positive_int(
                intelligent_reference_trades,
                "intelligent_reference_trades",
            )
        )

    # --------------------------------------------------------
    # SAFE FLOAT
    # --------------------------------------------------------

    def _safe_float(
        self,
        value,
        default=0.0,
    ):
        try:
            return float(value)

        except (
            TypeError,
            ValueError,
        ):
            return float(default)

    # --------------------------------------------------------
    # POSITIVE INTEGER VALIDATION
    # --------------------------------------------------------

    def _validate_positive_int(
        self,
        value,
        name,
    ):
        if isinstance(value, bool):
            raise ValueError(
                f"{name} must be a positive integer"
            )

        try:
            numeric = float(value)

        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                f"{name} must be a positive integer"
            )

        if not numeric.is_integer():
            raise ValueError(
                f"{name} must be a positive integer"
            )

        numeric = int(numeric)

        if numeric <= 0:
            raise ValueError(
                f"{name} must be greater than zero"
            )

        return numeric

    # --------------------------------------------------------
    # PERCENTAGE VALIDATION
    # --------------------------------------------------------

    def _validate_percentage(
        self,
        value,
        name,
        minimum=0.0,
        maximum=100.0,
    ):
        if isinstance(value, bool):
            raise ValueError(
                f"{name} must be numeric"
            )

        try:
            numeric = float(value)

        except (
            TypeError,
            ValueError,
        ):
            raise ValueError(
                f"{name} must be numeric"
            )

        if numeric < minimum:
            raise ValueError(
                f"{name} cannot be below {minimum}"
            )

        if numeric > maximum:
            raise ValueError(
                f"{name} cannot exceed {maximum}"
            )

        return numeric

    # --------------------------------------------------------
    # MODE NORMALIZATION
    # --------------------------------------------------------

    def _normalize_mode(
        self,
        mode,
    ):
        normalized = str(mode).strip().upper()

        aliases = {
            "FIXED": self.MODE_FIXED,
            "FIXED_TRADE": self.MODE_FIXED,
            "FIXED_TRADE_ALLOCATION": self.MODE_FIXED,

            "INTELLIGENT": self.MODE_INTELLIGENT,
            "SMART": self.MODE_INTELLIGENT,
            "DYNAMIC": self.MODE_INTELLIGENT,
            "INTELLIGENT_DYNAMIC": self.MODE_INTELLIGENT,
        }

        if normalized not in aliases:
            raise ValueError(
                "allocation_mode must be FIXED "
                "or INTELLIGENT"
            )

        return aliases[normalized]

    # --------------------------------------------------------
    # CONFIDENCE MULTIPLIER
    # --------------------------------------------------------

    def get_confidence_multiplier(
        self,
        setup_score,
    ):
        """
        Convert the internal setup-quality score into a
        risk-allocation multiplier.

        IMPORTANT:
        A score of 95 means 95/100 internal setup quality.
        It does NOT mean a guaranteed 95% win probability.

        Initial model:

            < 60       -> 0.00
            60-74.99   -> 0.50
            75-89.99   -> 0.75
            90-94.99   -> 1.00
            95-100     -> 1.25

        These thresholds will later become dashboard
        configuration and can be calibrated using paper-
        trading results.
        """

        score = self._validate_percentage(
            setup_score,
            "setup_score",
        )

        if score < self.minimum_setup_score:
            return 0.0

        if not self.confidence_scaling_enabled:
            return 1.0

        if score >= 95.0:
            return 1.25

        if score >= 90.0:
            return 1.00

        if score >= 75.0:
            return 0.75

        return 0.50

    # --------------------------------------------------------
    # BLOCKED RESULT
    # --------------------------------------------------------

    def _blocked_result(
        self,
        reason,
        daily_risk_budget_rupees=0.0,
        remaining_daily_risk_rupees=0.0,
        trades_taken_today=0,
        setup_score=0.0,
        risk_permission="BLOCK",
        hard_blocks=None,
    ):

        daily_budget = max(
            self._safe_float(
                daily_risk_budget_rupees
            ),
            0.0,
        )

        remaining = max(
            self._safe_float(
                remaining_daily_risk_rupees
            ),
            0.0,
        )

        trades_taken = max(
            int(
                self._safe_float(
                    trades_taken_today
                )
            ),
            0,
        )

        trades_remaining = max(
            self.max_trades_per_day
            - trades_taken,
            0,
        )

        return {
            "allocation_permission": "BLOCK",
            "allocation_allowed": False,
            "reason": reason,

            "allocation_mode": self.allocation_mode,

            "risk_permission": str(
                risk_permission
            ).upper(),

            "daily_risk_budget_rupees": round(
                daily_budget,
                2,
            ),

            "remaining_daily_risk_rupees": round(
                min(
                    remaining,
                    daily_budget,
                )
                if daily_budget > 0
                else remaining,
                2,
            ),

            "trades_taken_today": trades_taken,
            "max_trades_per_day": (
                self.max_trades_per_day
            ),
            "trades_remaining": trades_remaining,

            "setup_score": self._safe_float(
                setup_score
            ),

            "confidence_multiplier": 0.0,

            "base_risk_allocation_rupees": 0.0,
            "confidence_adjusted_risk_rupees": 0.0,
            "single_trade_cap_rupees": 0.0,
            "approved_risk_rupees": 0.0,

            "daily_budget_utilization_pct": 0.0,

            "hard_blocks": list(
                hard_blocks or []
            ),
        }

    # --------------------------------------------------------
    # ALLOCATION
    # --------------------------------------------------------

    def allocate(
        self,
        daily_risk_budget_rupees: float,
        remaining_daily_risk_rupees: float,
        trades_taken_today: int,
        setup_score: float,
        risk_permission: str = "ALLOW",
        entry_allowed: bool = True,
        hard_blocks: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Allocate risk for the NEXT trade.

        Parameters
        ----------
        daily_risk_budget_rupees:
            User's total permitted risk budget for the day.

        remaining_daily_risk_rupees:
            Risk budget still available for new trades.

            This should be supplied by the future session/
            portfolio state manager using actual trading
            outcomes.

        trades_taken_today:
            Number of new trade attempts already used.

        setup_score:
            Internal setup-quality score from 0 to 100.

        risk_permission:
            Upstream RiskManagementEngine permission.

        entry_allowed:
            Upstream safety permission.

        hard_blocks:
            Optional list of upstream hard-block reasons.

        Returns
        -------
        Dictionary containing the approved rupee risk for
        the next trade.

        No order is placed.
        """

        hard_blocks = list(
            hard_blocks or []
        )

        # ----------------------------------------------------
        # NORMALIZE INPUTS
        # ----------------------------------------------------

        daily_budget = self._safe_float(
            daily_risk_budget_rupees
        )

        remaining_budget = self._safe_float(
            remaining_daily_risk_rupees
        )

        trades_taken = int(
            self._safe_float(
                trades_taken_today
            )
        )

        risk_permission = str(
            risk_permission
        ).upper()

        # ----------------------------------------------------
        # BASIC SAFETY
        # ----------------------------------------------------

        if daily_budget <= 0:
            return self._blocked_result(
                reason="NO_DAILY_RISK_BUDGET",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=setup_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        if remaining_budget <= 0:
            return self._blocked_result(
                reason="DAILY_RISK_BUDGET_EXHAUSTED",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=0.0,
                trades_taken_today=trades_taken,
                setup_score=setup_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        # Never trust a remaining-risk value larger than
        # the user's original daily risk budget.
        remaining_budget = min(
            remaining_budget,
            daily_budget,
        )

        if trades_taken < 0:
            return self._blocked_result(
                reason="INVALID_TRADE_COUNT",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=0,
                setup_score=setup_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        # ----------------------------------------------------
        # UPSTREAM HARD BLOCK
        # ----------------------------------------------------

        if (
            risk_permission == "BLOCK"
            or not bool(entry_allowed)
            or hard_blocks
        ):
            return self._blocked_result(
                reason="UPSTREAM_RISK_BLOCK",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=setup_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        # ----------------------------------------------------
        # MAX TRADE ATTEMPTS
        # ----------------------------------------------------

        if trades_taken >= self.max_trades_per_day:
            return self._blocked_result(
                reason="MAX_TRADES_PER_DAY_REACHED",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=setup_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        trades_remaining = (
            self.max_trades_per_day
            - trades_taken
        )

        # ----------------------------------------------------
        # SETUP SCORE
        # ----------------------------------------------------

        try:
            normalized_score = (
                self._validate_percentage(
                    setup_score,
                    "setup_score",
                )
            )

        except ValueError:
            return self._blocked_result(
                reason="INVALID_SETUP_SCORE",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=0.0,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        if normalized_score < self.minimum_setup_score:
            return self._blocked_result(
                reason="SETUP_SCORE_BELOW_MINIMUM",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=normalized_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        # ----------------------------------------------------
        # CONFIDENCE MULTIPLIER
        # ----------------------------------------------------

        confidence_multiplier = (
            self.get_confidence_multiplier(
                normalized_score
            )
        )

        if confidence_multiplier <= 0:
            return self._blocked_result(
                reason="NO_CONFIDENCE_RISK_ALLOCATION",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=normalized_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        # ====================================================
        # FIXED MODE
        # ====================================================

        if self.allocation_mode == self.MODE_FIXED:

            # Divide remaining risk across remaining trade
            # opportunities.
            #
            # Example:
            #
            # ₹10,000 remaining / 3 trades = ₹3,333
            #
            # If actual loss was smaller than expected:
            #
            # ₹8,500 remaining / 2 trades = ₹4,250
            #
            # This means we use ACTUAL remaining risk rather
            # than pretending every previous trade hit full SL.

            base_allocation = (
                remaining_budget
                / trades_remaining
            )

        # ====================================================
        # INTELLIGENT MODE
        # ====================================================

        else:

            # Intelligent mode does not blindly divide the
            # whole remaining budget by remaining trade count.
            #
            # It creates a reference allocation using a
            # configurable reference number of trades.
            #
            # Example:
            #
            # Daily budget = ₹50,000
            # Reference trades = 3
            #
            # Reference allocation = ₹16,667
            #
            # Setup confidence then increases/reduces this
            # amount subject to all hard limits.

            reference_allocation = (
                daily_budget
                / self.intelligent_reference_trades
            )

            # Do not let the reference allocation itself
            # exceed remaining risk.
            base_allocation = min(
                reference_allocation,
                remaining_budget,
            )

        # ----------------------------------------------------
        # CONFIDENCE ADJUSTMENT
        # ----------------------------------------------------

        confidence_adjusted = (
            base_allocation
            * confidence_multiplier
        )

        # ----------------------------------------------------
        # MAXIMUM SINGLE-TRADE RISK CAP
        # ----------------------------------------------------

        single_trade_cap = (
            daily_budget
            * (
                self.max_single_trade_daily_risk_pct
                / 100.0
            )
        )

        # ----------------------------------------------------
        # FINAL APPROVED RISK
        # ----------------------------------------------------

        approved_risk = min(
            confidence_adjusted,
            single_trade_cap,
            remaining_budget,
        )

        approved_risk = max(
            approved_risk,
            0.0,
        )

        if approved_risk <= 0:
            return self._blocked_result(
                reason="NO_RISK_AVAILABLE_FOR_TRADE",
                daily_risk_budget_rupees=daily_budget,
                remaining_daily_risk_rupees=remaining_budget,
                trades_taken_today=trades_taken,
                setup_score=normalized_score,
                risk_permission=risk_permission,
                hard_blocks=hard_blocks,
            )

        # ----------------------------------------------------
        # ABSOLUTE SAFETY ASSERTIONS
        # ----------------------------------------------------

        # These are intentionally explicit.
        # Even future modifications should never allow these
        # conditions to become true.

        if approved_risk > remaining_budget:
            raise RuntimeError(
                "SAFETY VIOLATION: approved risk exceeds "
                "remaining daily risk"
            )

        if approved_risk > daily_budget:
            raise RuntimeError(
                "SAFETY VIOLATION: approved risk exceeds "
                "daily risk budget"
            )

        if approved_risk > single_trade_cap:
            raise RuntimeError(
                "SAFETY VIOLATION: approved risk exceeds "
                "single-trade risk cap"
            )

        # ----------------------------------------------------
        # UTILIZATION
        # ----------------------------------------------------

        utilization_pct = (
            approved_risk
            / daily_budget
            * 100.0
        )

        # ----------------------------------------------------
        # SUCCESS
        # ----------------------------------------------------

        return {
            "allocation_permission": "ALLOW",
            "allocation_allowed": True,
            "reason": "RISK_BUDGET_ALLOCATED",

            "allocation_mode": (
                self.allocation_mode
            ),

            "risk_permission": (
                risk_permission
            ),

            "daily_risk_budget_rupees": round(
                daily_budget,
                2,
            ),

            "remaining_daily_risk_rupees": round(
                remaining_budget,
                2,
            ),

            "trades_taken_today": (
                trades_taken
            ),

            "max_trades_per_day": (
                self.max_trades_per_day
            ),

            "trades_remaining": (
                trades_remaining
            ),

            "setup_score": round(
                normalized_score,
                2,
            ),

            "confidence_scaling_enabled": (
                self.confidence_scaling_enabled
            ),

            "confidence_multiplier": round(
                confidence_multiplier,
                4,
            ),

            "base_risk_allocation_rupees": round(
                base_allocation,
                2,
            ),

            "confidence_adjusted_risk_rupees": round(
                confidence_adjusted,
                2,
            ),

            "single_trade_cap_pct": (
                self.max_single_trade_daily_risk_pct
            ),

            "single_trade_cap_rupees": round(
                single_trade_cap,
                2,
            ),

            "approved_risk_rupees": round(
                approved_risk,
                2,
            ),

            "daily_budget_utilization_pct": round(
                utilization_pct,
                4,
            ),

            "hard_blocks": [],
        }

    # --------------------------------------------------------
    # ALIAS
    # --------------------------------------------------------

    def analyze(
        self,
        daily_risk_budget_rupees,
        remaining_daily_risk_rupees,
        trades_taken_today,
        setup_score,
        risk_permission="ALLOW",
        entry_allowed=True,
        hard_blocks=None,
    ):
        """
        Alias for allocate().
        """

        return self.allocate(
            daily_risk_budget_rupees=(
                daily_risk_budget_rupees
            ),
            remaining_daily_risk_rupees=(
                remaining_daily_risk_rupees
            ),
            trades_taken_today=(
                trades_taken_today
            ),
            setup_score=setup_score,
            risk_permission=risk_permission,
            entry_allowed=entry_allowed,
            hard_blocks=hard_blocks,
        )


    