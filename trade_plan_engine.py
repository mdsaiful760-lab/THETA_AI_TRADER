# ============================================================
# THETA AI TRADER — TRADE PLAN ENGINE
# ============================================================

from datetime import datetime, timezone
from uuid import uuid4


class TradePlanEngine:
    """
    Converts an APPROVED TradeRiskOrchestrator result into a
    validated ORDER INTENT.

    IMPORTANT
    ---------
    This engine:

    - DOES NOT select option strikes
    - DOES NOT calculate position size
    - DOES NOT increase authorized quantity
    - DOES NOT place broker orders
    - DOES NOT connect to Zerodha

    It only validates and packages an already-authorized trade
    into a standardized order intent.
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

    VALID_ENTRY_TYPES = {
        "MARKET",
        "LIMIT",
    }

    VALID_STOP_LOSS_TYPES = {
        "PREMIUM_POINTS",
        "PREMIUM_PERCENT",
        "UNDERLYING_POINTS",
        "UNDERLYING_LEVEL",
        "TECHNICAL_LEVEL",
    }

    VALID_EXIT_MODES = {
        "STOP_LOSS_ONLY",
        "STOP_LOSS_TARGET",
        "TRAILING_STOP",
        "TIME_EXIT",
        "STRATEGY_EXIT",
        "HYBRID",
    }

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
    # NORMALIZE TEXT
    # --------------------------------------------------------

    def _normalize_text(
        self,
        value,
    ):

        if value is None:
            return ""

        return str(value).strip().upper()

    # --------------------------------------------------------
    # CREATE TRADE ID
    # --------------------------------------------------------

    def _create_trade_id(self):

        timestamp = datetime.now(
            timezone.utc
        ).strftime(
            "%Y%m%d%H%M%S%f"
        )

        unique = uuid4().hex[:10].upper()

        return (
            f"THETA-{timestamp}-{unique}"
        )

    # --------------------------------------------------------
    # CURRENT UTC TIME
    # --------------------------------------------------------

    def _utc_timestamp(self):

        return datetime.now(
            timezone.utc
        ).isoformat()

    # --------------------------------------------------------
    # BLOCKED RESULT
    # --------------------------------------------------------

    def _blocked_result(
        self,
        reason,
        validation_errors=None,
        orchestrator_result=None,
    ):

        return {
            "plan_permission":
                "BLOCK",

            "plan_allowed":
                False,

            "reason":
                reason,

            "validation_errors":
                list(
                    validation_errors
                    or []
                ),

            "trade_id":
                None,

            "order_intent_created":
                False,

            "broker_order_allowed":
                False,

            "order_intent":
                None,

            "orchestrator_permission":
                (
                    orchestrator_result.get(
                        "final_permission"
                    )
                    if isinstance(
                        orchestrator_result,
                        dict,
                    )
                    else None
                ),

            "authorized_lots":
                (
                    orchestrator_result.get(
                        "final_lots",
                        0,
                    )
                    if isinstance(
                        orchestrator_result,
                        dict,
                    )
                    else 0
                ),

            "authorized_quantity":
                (
                    orchestrator_result.get(
                        "final_quantity",
                        0,
                    )
                    if isinstance(
                        orchestrator_result,
                        dict,
                    )
                    else 0
                ),

            "final_authorized_risk_rupees":
                (
                    orchestrator_result.get(
                        "final_authorized_risk_rupees",
                        0.0,
                    )
                    if isinstance(
                        orchestrator_result,
                        dict,
                    )
                    else 0.0
                ),
        }

    # --------------------------------------------------------
    # VALIDATE ORCHESTRATOR AUTHORITY
    # --------------------------------------------------------

    def _validate_orchestrator(
        self,
        orchestrator_result,
    ):

        errors = []

        if not isinstance(
            orchestrator_result,
            dict,
        ):

            return [
                "INVALID_ORCHESTRATOR_RESULT"
            ]

        if (
            orchestrator_result.get(
                "final_permission"
            )
            != "ALLOW"
        ):

            errors.append(
                "ORCHESTRATOR_PERMISSION_NOT_ALLOW"
            )

        if (
            orchestrator_result.get(
                "trade_allowed"
            )
            is not True
        ):

            errors.append(
                "ORCHESTRATOR_TRADE_NOT_ALLOWED"
            )

        authorized_lots = self._safe_int(
            orchestrator_result.get(
                "final_lots",
                0,
            )
        )

        authorized_quantity = (
            self._safe_int(
                orchestrator_result.get(
                    "final_quantity",
                    0,
                )
            )
        )

        authorized_risk = (
            self._safe_float(
                orchestrator_result.get(
                    "final_authorized_risk_rupees",
                    0.0,
                )
            )
        )

        estimated_max_loss = (
            self._safe_float(
                orchestrator_result.get(
                    "estimated_max_loss",
                    0.0,
                )
            )
        )

        if authorized_lots <= 0:

            errors.append(
                "NO_AUTHORIZED_LOTS"
            )

        if authorized_quantity <= 0:

            errors.append(
                "NO_AUTHORIZED_QUANTITY"
            )

        if authorized_risk <= 0:

            errors.append(
                "NO_AUTHORIZED_RISK"
            )

        if estimated_max_loss < 0:

            errors.append(
                "INVALID_ESTIMATED_MAX_LOSS"
            )

        if (
            estimated_max_loss
            > authorized_risk
            + 0.01
        ):

            errors.append(
                "ESTIMATED_LOSS_EXCEEDS_AUTHORIZED_RISK"
            )

        return errors

    # --------------------------------------------------------
    # CREATE ORDER INTENT
    # --------------------------------------------------------

    def create_plan(
        self,
        orchestrator_result,
        underlying,
        exchange,
        tradingsymbol,
        expiry,
        strike,
        option_type,
        side,
        lot_size,
        entry_type="MARKET",
        entry_reference_price=None,
        stop_loss_type="PREMIUM_POINTS",
        stop_loss_value=None,
        target_value=None,
        exit_mode="STOP_LOSS_ONLY",
        requested_lots=None,
        requested_quantity=None,
        strategy_name=None,
        strategy_id=None,
        signal_id=None,
        notes=None,
        metadata=None,
    ):
        """
        Create a validated order intent.

        The orchestrator remains the source of truth for
        position authority.

        requested_lots/requested_quantity may REDUCE an
        authorized position, but may never increase it.
        """

        # ====================================================
        # STAGE 1 — VALIDATE UPSTREAM AUTHORITY
        # ====================================================

        orchestrator_errors = (
            self._validate_orchestrator(
                orchestrator_result
            )
        )

        if orchestrator_errors:

            return self._blocked_result(
                reason=(
                    "ORCHESTRATOR_AUTHORITY_INVALID"
                ),
                validation_errors=(
                    orchestrator_errors
                ),
                orchestrator_result=(
                    orchestrator_result
                ),
            )

        # ====================================================
        # READ AUTHORIZED POSITION
        # ====================================================

        authorized_lots = self._safe_int(
            orchestrator_result[
                "final_lots"
            ]
        )

        authorized_quantity = (
            self._safe_int(
                orchestrator_result[
                    "final_quantity"
                ]
            )
        )

        authorized_risk = (
            self._safe_float(
                orchestrator_result[
                    "final_authorized_risk_rupees"
                ]
            )
        )

        estimated_max_loss = (
            self._safe_float(
                orchestrator_result.get(
                    "estimated_max_loss",
                    0.0,
                )
            )
        )

        # ====================================================
        # NORMALIZE CONTRACT INFORMATION
        # ====================================================

        underlying = (
            self._normalize_text(
                underlying
            )
        )

        exchange = (
            self._normalize_text(
                exchange
            )
        )

        tradingsymbol = (
            self._normalize_text(
                tradingsymbol
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

        entry_type = (
            self._normalize_text(
                entry_type
            )
        )

        stop_loss_type = (
            self._normalize_text(
                stop_loss_type
            )
        )

        exit_mode = (
            self._normalize_text(
                exit_mode
            )
        )

        strike = self._safe_float(
            strike
        )

        lot_size = self._safe_int(
            lot_size
        )

        # ====================================================
        # DETERMINE REQUESTED LOTS / QUANTITY
        # ====================================================

        if requested_lots is None:

            final_lots = (
                authorized_lots
            )

        else:

            final_lots = (
                self._safe_int(
                    requested_lots
                )
            )

        if requested_quantity is None:

            final_quantity = (
                final_lots
                * lot_size
            )

        else:

            final_quantity = (
                self._safe_int(
                    requested_quantity
                )
            )

        # ====================================================
        # STAGE 2 — VALIDATION
        # ====================================================

        errors = []

        if not underlying:

            errors.append(
                "UNDERLYING_REQUIRED"
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
                "TRADINGSYMBOL_REQUIRED"
            )

        if expiry is None:

            errors.append(
                "EXPIRY_REQUIRED"
            )

        elif not str(
            expiry
        ).strip():

            errors.append(
                "EXPIRY_REQUIRED"
            )

        if strike <= 0:

            errors.append(
                "INVALID_STRIKE"
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

        if lot_size <= 0:

            errors.append(
                "INVALID_LOT_SIZE"
            )

        if (
            entry_type
            not in self.VALID_ENTRY_TYPES
        ):

            errors.append(
                "INVALID_ENTRY_TYPE"
            )

        if (
            entry_type == "LIMIT"
        ):

            limit_price = (
                self._safe_float(
                    entry_reference_price
                )
            )

            if limit_price <= 0:

                errors.append(
                    "LIMIT_PRICE_REQUIRED"
                )

        if (
            stop_loss_type
            not in self.VALID_STOP_LOSS_TYPES
        ):

            errors.append(
                "INVALID_STOP_LOSS_TYPE"
            )

        stop_loss_numeric = (
            self._safe_float(
                stop_loss_value
            )
        )

        if stop_loss_numeric <= 0:

            errors.append(
                "INVALID_STOP_LOSS_VALUE"
            )

        if (
            exit_mode
            not in self.VALID_EXIT_MODES
        ):

            errors.append(
                "INVALID_EXIT_MODE"
            )

        if final_lots <= 0:

            errors.append(
                "INVALID_REQUESTED_LOTS"
            )

        if (
            final_lots
            > authorized_lots
        ):

            errors.append(
                "REQUESTED_LOTS_EXCEED_AUTHORITY"
            )

        if final_quantity <= 0:

            errors.append(
                "INVALID_REQUESTED_QUANTITY"
            )

        if (
            final_quantity
            > authorized_quantity
        ):

            errors.append(
                "REQUESTED_QUANTITY_EXCEEDS_AUTHORITY"
            )

        if (
            lot_size > 0
            and final_lots > 0
            and final_quantity
            != (
                final_lots
                * lot_size
            )
        ):

            errors.append(
                "LOT_QUANTITY_MISMATCH"
            )

        # ====================================================
        # AUTHORIZED QUANTITY CONSISTENCY
        # ====================================================

        if (
            lot_size > 0
            and authorized_lots > 0
            and authorized_quantity
            != (
                authorized_lots
                * lot_size
            )
        ):

            errors.append(
                "AUTHORIZED_POSITION_LOT_SIZE_MISMATCH"
            )

        # ====================================================
        # RISK CANNOT INCREASE
        # ====================================================

        if (
            estimated_max_loss
            > authorized_risk
            + 0.01
        ):

            errors.append(
                "RISK_AUTHORITY_VIOLATION"
            )

        # ====================================================
        # TARGET VALIDATION
        # ====================================================

        if (
            exit_mode
            == "STOP_LOSS_TARGET"
        ):

            if (
                self._safe_float(
                    target_value
                )
                <= 0
            ):

                errors.append(
                    "TARGET_REQUIRED"
                )

        # ====================================================
        # BLOCK INVALID PLAN
        # ====================================================

        if errors:

            return self._blocked_result(
                reason=(
                    "TRADE_PLAN_VALIDATION_FAILED"
                ),
                validation_errors=errors,
                orchestrator_result=(
                    orchestrator_result
                ),
            )

        # ====================================================
        # CREATE UNIQUE INTENT
        # ====================================================

        trade_id = (
            self._create_trade_id()
        )

        created_at = (
            self._utc_timestamp()
        )

        # ====================================================
        # ORDER INTENT
        # ====================================================

        order_intent = {
            "trade_id":
                trade_id,

            "created_at_utc":
                created_at,

            # ------------------------------------------------
            # CONTRACT
            # ------------------------------------------------

            "underlying":
                underlying,

            "exchange":
                exchange,

            "tradingsymbol":
                tradingsymbol,

            "expiry":
                str(expiry),

            "strike":
                strike,

            "option_type":
                option_type,

            # ------------------------------------------------
            # ORDER
            # ------------------------------------------------

            "side":
                side,

            "entry_type":
                entry_type,

            "entry_reference_price":
                (
                    self._safe_float(
                        entry_reference_price
                    )
                    if (
                        entry_reference_price
                        is not None
                    )
                    else None
                ),

            # ------------------------------------------------
            # POSITION
            # ------------------------------------------------

            "lot_size":
                lot_size,

            "lots":
                final_lots,

            "quantity":
                final_quantity,

            "authorized_lots":
                authorized_lots,

            "authorized_quantity":
                authorized_quantity,

            # ------------------------------------------------
            # RISK
            # ------------------------------------------------

            "final_authorized_risk_rupees":
                round(
                    authorized_risk,
                    2,
                ),

            "estimated_max_loss":
                round(
                    estimated_max_loss,
                    2,
                ),

            "setup_score":
                self._safe_float(
                    orchestrator_result.get(
                        "setup_score",
                        0.0,
                    )
                ),

            # ------------------------------------------------
            # EXIT PLAN
            # ------------------------------------------------

            "stop_loss_type":
                stop_loss_type,

            "stop_loss_value":
                stop_loss_numeric,

            "target_value":
                (
                    self._safe_float(
                        target_value
                    )
                    if (
                        target_value
                        is not None
                    )
                    else None
                ),

            "exit_mode":
                exit_mode,

            # ------------------------------------------------
            # STRATEGY TRACEABILITY
            # ------------------------------------------------

            "strategy_name":
                strategy_name,

            "strategy_id":
                strategy_id,

            "signal_id":
                signal_id,

            "notes":
                notes,

            "metadata":
                dict(
                    metadata
                    or {}
                ),

            # ------------------------------------------------
            # EXECUTION SAFETY
            # ------------------------------------------------

            "broker_order_allowed":
                False,

            "execution_status":
                "INTENT_ONLY",

            "broker_order_id":
                None,
        }

        # ====================================================
        # FINAL SAFETY ASSERTIONS
        # ====================================================

        if (
            order_intent["lots"]
            > authorized_lots
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: trade plan lots "
                "exceed orchestrator authority"
            )

        if (
            order_intent["quantity"]
            > authorized_quantity
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: trade plan quantity "
                "exceeds orchestrator authority"
            )

        if (
            order_intent[
                "estimated_max_loss"
            ]
            > order_intent[
                "final_authorized_risk_rupees"
            ]
            + 0.01
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: trade plan risk "
                "exceeds orchestrator authority"
            )

        if (
            order_intent[
                "broker_order_allowed"
            ]
            is not False
        ):

            raise RuntimeError(
                "SAFETY VIOLATION: TradePlanEngine "
                "received broker-order authority"
            )

        # ====================================================
        # SUCCESS RESULT
        # ====================================================

        return {
            "plan_permission":
                "ALLOW",

            "plan_allowed":
                True,

            "reason":
                "ORDER_INTENT_CREATED",

            "validation_errors":
                [],

            "trade_id":
                trade_id,

            "order_intent_created":
                True,

            "broker_order_allowed":
                False,

            "authorized_lots":
                authorized_lots,

            "authorized_quantity":
                authorized_quantity,

            "final_authorized_risk_rupees":
                round(
                    authorized_risk,
                    2,
                ),

            "order_intent":
                order_intent,
        }

    # --------------------------------------------------------
    # FRIENDLY ALIAS
    # --------------------------------------------------------

    def build_plan(
        self,
        **kwargs,
    ):

        return self.create_plan(
            **kwargs
        )