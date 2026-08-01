# ============================================================
# THETA AI TRADER — TRADE RISK ORCHESTRATOR
# ============================================================

from config_manager import ConfigManager
from risk_management_engine import RiskManagementEngine
from risk_budget_allocator import RiskBudgetAllocator
from position_sizing_engine import PositionSizingEngine


class TradeRiskOrchestrator:
    """
    Central orchestration layer for trade-risk authorization.

    Pipeline
    --------

        Signal Analysis
              ↓
        RiskManagementEngine
              ↓
        RiskBudgetAllocator
              ↓
        Final Authorized Risk
              ↓
        PositionSizingEngine
              ↓
        Final Position Decision

    IMPORTANT
    ---------
    This class does NOT place orders.

    It only determines:

    - whether a trade may proceed
    - how much rupee risk is authorized
    - how many lots may be used
    - final quantity
    - why a trade was blocked
    """

    def __init__(
        self,
        config_manager=None,
        risk_engine=None,
        risk_budget_allocator=None,
        position_sizing_engine=None,
        use_dynamic_config=True,
    ):

        self.config_manager = (
            config_manager
            or ConfigManager()
        )

        self.use_dynamic_config = bool(
            use_dynamic_config
        )

        self.risk_engine = (
            risk_engine
            or RiskManagementEngine(
                config_manager=self.config_manager,
                use_dynamic_config=self.use_dynamic_config,
            )
        )

        self.risk_budget_allocator = (
            risk_budget_allocator
            or RiskBudgetAllocator(
                config_manager=self.config_manager,
                use_dynamic_config=self.use_dynamic_config,
            )
        )

        self.position_sizing_engine = (
            position_sizing_engine
            or PositionSizingEngine(
                config_manager=self.config_manager,
                use_dynamic_config=self.use_dynamic_config,
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

            if value is None:
                return float(default)

            return float(value)

        except (
            TypeError,
            ValueError,
        ):

            return float(default)

    # --------------------------------------------------------
    # SAFE INTEGER
    # --------------------------------------------------------

    def _safe_int(
        self,
        value,
        default=0,
    ):

        try:

            if value is None:
                return int(default)

            return int(value)

        except (
            TypeError,
            ValueError,
        ):

            return int(default)

    # --------------------------------------------------------
    # CALCULATE DAILY RISK BUDGET
    # --------------------------------------------------------

    def _calculate_daily_risk_budget(
        self,
        capital,
    ):
        """
        Daily risk budget comes from the user's configured
        maximum daily loss percentage.

        Example:

            Capital = ₹10,00,000
            Daily loss limit = 3%

            Daily risk budget = ₹30,000
        """

        capital = max(
            self._safe_float(capital),
            0.0,
        )

        daily_loss_pct = max(
            self._safe_float(
                self.risk_engine.max_daily_loss_pct
            ),
            0.0,
        )

        return (
            capital
            * daily_loss_pct
            / 100.0
        )

    # --------------------------------------------------------
    # CALCULATE REMAINING DAILY RISK
    # --------------------------------------------------------

    def _calculate_remaining_daily_risk(
        self,
        daily_risk_budget_rupees,
        daily_pnl,
    ):
        """
        Only realized/current negative daily P&L consumes
        the daily loss budget here.

        Positive P&L does NOT create additional risk budget.

        Example:

            Daily budget = ₹30,000
            Current daily P&L = -₹8,000

            Remaining risk = ₹22,000
        """

        daily_budget = max(
            self._safe_float(
                daily_risk_budget_rupees
            ),
            0.0,
        )

        daily_pnl = self._safe_float(
            daily_pnl
        )

        consumed_loss = max(
            -daily_pnl,
            0.0,
        )

        remaining = (
            daily_budget
            - consumed_loss
        )

        return max(
            min(
                remaining,
                daily_budget,
            ),
            0.0,
        )

    # --------------------------------------------------------
    # STANDARD BLOCKED POSITION RESULT
    # --------------------------------------------------------

    def _zero_position_result(
        self,
        reason,
        risk_permission="BLOCK",
        is_expiry_day=False,
    ):

        return (
            self.position_sizing_engine
            ._blocked_result(
                reason=reason,
                risk_permission=risk_permission,
                is_expiry_day=is_expiry_day,
            )
        )

    # --------------------------------------------------------
    # FINAL DECISION
    # --------------------------------------------------------

    def analyze(
        self,
        capital,
        signal_analysis,
        setup_score,
        lot_size,
        stop_loss_per_unit,
        margin_per_lot,
        available_margin,
        account_state=None,
        volatility=None,
        session=None,
        trades_taken_today=0,
        is_expiry_day=False,
        remaining_daily_risk_rupees=None,
    ):
        """
        Run the complete risk → allocation → sizing pipeline.

        No order is placed.
        """

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

        trades_taken_today = max(
            self._safe_int(
                trades_taken_today
            ),
            0,
        )

        # ====================================================
        # STAGE 1 — RISK MANAGEMENT ENGINE
        # ====================================================

        risk_result = (
            self.risk_engine.analyze(
                capital=capital,
                signal_analysis=signal_analysis,
                account_state=account_state,
                volatility=volatility,
                session=session,
                is_expiry_day=is_expiry_day,
            )
        )

        # ====================================================
        # DAILY RISK BUDGET
        # ====================================================

        daily_risk_budget = (
            self._calculate_daily_risk_budget(
                capital=capital
            )
        )

        # ====================================================
        # REMAINING DAILY RISK
        # ====================================================

        if remaining_daily_risk_rupees is None:

            remaining_daily_risk = (
                self._calculate_remaining_daily_risk(
                    daily_risk_budget_rupees=(
                        daily_risk_budget
                    ),
                    daily_pnl=account_state.get(
                        "daily_pnl",
                        0.0,
                    ),
                )
            )

        else:

            remaining_daily_risk = max(
                self._safe_float(
                    remaining_daily_risk_rupees
                ),
                0.0,
            )

            remaining_daily_risk = min(
                remaining_daily_risk,
                daily_risk_budget,
            )

        # ====================================================
        # STAGE 2 — RISK BUDGET ALLOCATOR
        # ====================================================

        allocation_result = (
            self.risk_budget_allocator.allocate(
                daily_risk_budget_rupees=(
                    daily_risk_budget
                ),
                remaining_daily_risk_rupees=(
                    remaining_daily_risk
                ),
                trades_taken_today=(
                    trades_taken_today
                ),
                setup_score=setup_score,
                risk_permission=risk_result.get(
                    "risk_permission",
                    "BLOCK",
                ),
                entry_allowed=risk_result.get(
                    "entry_allowed",
                    False,
                ),
                hard_blocks=risk_result.get(
                    "hard_blocks",
                    [],
                ),
            )
        )

        # ====================================================
        # UPSTREAM RISK CEILINGS
        # ====================================================

        risk_engine_limit = max(
            self._safe_float(
                risk_result.get(
                    "allowed_risk_rupees",
                    0.0,
                )
            ),
            0.0,
        )

        allocator_limit = max(
            self._safe_float(
                allocation_result.get(
                    "approved_risk_rupees",
                    0.0,
                )
            ),
            0.0,
        )

        # ====================================================
        # FINAL AUTHORIZED RISK
        # ====================================================
        #
        # CRITICAL SAFETY PRINCIPLE:
        #
        # Neither downstream engine may increase authority
        # granted by an upstream protection layer.
        #
        # Therefore final risk is always the smaller of:
        #
        #   RiskManagementEngine ceiling
        #   RiskBudgetAllocator allocation
        #

        final_authorized_risk = min(
            risk_engine_limit,
            allocator_limit,
        )

        final_authorized_risk = max(
            final_authorized_risk,
            0.0,
        )

        # ====================================================
        # DETERMINE WHETHER SIZING MAY RUN
        # ====================================================

        upstream_allowed = bool(
            risk_result.get(
                "entry_allowed",
                False,
            )
        )

        allocation_allowed = bool(
            allocation_result.get(
                "allocation_allowed",
                False,
            )
        )

        if (
            not upstream_allowed
            or not allocation_allowed
            or final_authorized_risk <= 0
        ):

            if not upstream_allowed:

                final_block_reason = (
                    "RISK_MANAGEMENT_BLOCK"
                )

            elif not allocation_allowed:

                final_block_reason = (
                    "RISK_BUDGET_BLOCK"
                )

            else:

                final_block_reason = (
                    "NO_FINAL_AUTHORIZED_RISK"
                )

            sizing_result = (
                self._zero_position_result(
                    reason=final_block_reason,
                    risk_permission="BLOCK",
                    is_expiry_day=is_expiry_day,
                )
            )

        else:

            # =================================================
            # ADAPTER FOR POSITION SIZING ENGINE
            # =================================================
            #
            # PositionSizingEngine expects:
            #
            #   risk_permission
            #   entry_allowed
            #   allowed_risk_rupees
            #
            # We intentionally replace allowed_risk_rupees
            # with the FINAL authorized amount.
            #

            sizing_risk_input = {
                "risk_permission":
                    risk_result.get(
                        "risk_permission",
                        "ALLOW",
                    ),

                "entry_allowed":
                    True,

                "allowed_risk_rupees":
                    final_authorized_risk,
            }

            # =================================================
            # STAGE 3 — POSITION SIZING ENGINE
            # =================================================

            sizing_result = (
                self.position_sizing_engine
                .analyze(
                    risk_analysis=(
                        sizing_risk_input
                    ),
                    lot_size=lot_size,
                    stop_loss_per_unit=(
                        stop_loss_per_unit
                    ),
                    margin_per_lot=(
                        margin_per_lot
                    ),
                    available_margin=(
                        available_margin
                    ),
                    is_expiry_day=(
                        is_expiry_day
                    ),
                )
            )

            final_block_reason = (
                None
                if sizing_result.get(
                    "position_allowed",
                    False,
                )
                else sizing_result.get(
                    "reason"
                )
            )

        # ====================================================
        # FINAL TRADE PERMISSION
        # ====================================================

        final_trade_allowed = bool(
            risk_result.get(
                "entry_allowed",
                False,
            )
            and allocation_result.get(
                "allocation_allowed",
                False,
            )
            and sizing_result.get(
                "position_allowed",
                False,
            )
            and final_authorized_risk > 0
            and sizing_result.get(
                "final_lots",
                0,
            ) > 0
        )

        final_permission = (
            "ALLOW"
            if final_trade_allowed
            else "BLOCK"
        )

        # ====================================================
        # FINAL SAFETY ASSERTIONS
        # ====================================================

        estimated_max_loss = max(
            self._safe_float(
                sizing_result.get(
                    "estimated_max_loss",
                    0.0,
                )
            ),
            0.0,
        )

        if (
            estimated_max_loss
            > final_authorized_risk
            + 0.01
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: estimated max loss "
                "exceeds final authorized risk"
            )

        if (
            final_authorized_risk
            > remaining_daily_risk
            + 0.01
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: final authorized risk "
                "exceeds remaining daily risk"
            )

        if (
            final_authorized_risk
            > daily_risk_budget
            + 0.01
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: final authorized risk "
                "exceeds daily risk budget"
            )

        if (
            not final_trade_allowed
            and sizing_result.get(
                "final_quantity",
                0,
            ) != 0
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: blocked trade has "
                "non-zero quantity"
            )

        # ====================================================
        # FINAL RESULT
        # ====================================================

        return {
            "final_permission":
                final_permission,

            "trade_allowed":
                final_trade_allowed,

            "final_block_reason":
                final_block_reason,

            "order_placement_enabled":
                False,

            # ------------------------------------------------
            # CAPITAL / DAILY RISK
            # ------------------------------------------------

            "capital":
                capital,

            "daily_risk_budget_rupees":
                round(
                    daily_risk_budget,
                    2,
                ),

            "remaining_daily_risk_rupees":
                round(
                    remaining_daily_risk,
                    2,
                ),

            # ------------------------------------------------
            # RISK AUTHORITY
            # ------------------------------------------------

            "risk_engine_limit_rupees":
                round(
                    risk_engine_limit,
                    2,
                ),

            "allocator_limit_rupees":
                round(
                    allocator_limit,
                    2,
                ),

            "final_authorized_risk_rupees":
                round(
                    final_authorized_risk,
                    2,
                ),

            # ------------------------------------------------
            # TRADE STATE
            # ------------------------------------------------

            "trades_taken_today":
                trades_taken_today,

            "setup_score":
                self._safe_float(
                    setup_score
                ),

            "is_expiry_day":
                bool(
                    is_expiry_day
                ),

            # ------------------------------------------------
            # FINAL POSITION
            # ------------------------------------------------

            "final_lots":
                sizing_result.get(
                    "final_lots",
                    0,
                ),

            "final_quantity":
                sizing_result.get(
                    "final_quantity",
                    0,
                ),

            "estimated_max_loss":
                sizing_result.get(
                    "estimated_max_loss",
                    0.0,
                ),

            "estimated_margin_required":
                sizing_result.get(
                    "estimated_margin_required",
                    0.0,
                ),

            "limiting_factor":
                sizing_result.get(
                    "limiting_factor",
                    "BLOCKED",
                ),

            # ------------------------------------------------
            # COMPLETE AUDIT TRAIL
            # ------------------------------------------------

            "risk_management":
                risk_result,

            "risk_budget":
                allocation_result,

            "position_sizing":
                sizing_result,
        }

    # --------------------------------------------------------
    # ALIAS
    # --------------------------------------------------------

    def evaluate_trade(
        self,
        **kwargs,
    ):
        """
        Friendly alias for analyze().
        """

        return self.analyze(
            **kwargs
        )