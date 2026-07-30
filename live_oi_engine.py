# ============================================================
# THETA AI TRADER — LIVE OI CYCLE ENGINE
# ============================================================

from datetime import datetime, time

from market_data_engine import MarketDataEngine
from option_snapshot_engine import OptionSnapshotEngine
from oi_engine import OIEngine


class LiveOIEngine:
    """
    Coordinates the live Open Interest analysis cycle.

    Flow:
    1. Fetch current NIFTY option-chain snapshot
    2. Load previous stored snapshot
    3. Validate expiry / contracts / timestamps / session
    4. Compare T1 vs T2
    5. Run whole-chain OI intelligence
    6. Save current snapshot for next cycle

    This engine does NOT:
    - Place orders
    - Modify positions
    - Select trading quantity
    """

    def __init__(
        self,
        market_data_engine=None,
        snapshot_engine=None,
        oi_engine=None,
        strike_range=10,
        min_snapshot_gap_minutes=1,
        max_snapshot_gap_minutes=15,
    ):
        self.market_data_engine = (
            market_data_engine
            or MarketDataEngine()
        )

        self.snapshot_engine = (
            snapshot_engine
            or OptionSnapshotEngine()
        )

        self.oi_engine = (
            oi_engine
            or OIEngine()
        )

        self.strike_range = int(
            strike_range
        )

        self.min_snapshot_gap_minutes = float(
            min_snapshot_gap_minutes
        )

        self.max_snapshot_gap_minutes = float(
            max_snapshot_gap_minutes
        )

        # NSE regular equity / derivatives session.
        self.market_open_time = time(
            9,
            15,
        )

        self.market_close_time = time(
            15,
            30,
        )

        if self.strike_range <= 0:
            raise ValueError(
                "strike_range must be greater than zero"
            )

        if self.min_snapshot_gap_minutes < 0:
            raise ValueError(
                "min_snapshot_gap_minutes cannot be negative"
            )

        if (
            self.max_snapshot_gap_minutes
            <= self.min_snapshot_gap_minutes
        ):
            raise ValueError(
                "max_snapshot_gap_minutes must be greater "
                "than min_snapshot_gap_minutes"
            )

    # --------------------------------------------------------
    # BUILD CURRENT SNAPSHOT
    # --------------------------------------------------------

    def fetch_current_snapshot(self):
        """
        Fetch the current option chain from Kite and convert
        it into the standard snapshot format.
        """

        data = (
            self.market_data_engine
            .get_nifty_option_snapshot(
                strike_range=self.strike_range
            )
        )

        if not data:
            raise RuntimeError(
                "No option-chain data returned"
            )

        options = data.get(
            "options",
            []
        )

        if not options:
            raise RuntimeError(
                "Current option snapshot is empty"
            )

        snapshot = {
            "timestamp": (
                datetime.now()
                .astimezone()
                .isoformat()
            ),

            "spot": float(
                data["spot"]
            ),

            "expiry": str(
                data["expiry"]
            ),

            "atm": float(
                data["atm"]
            ),

            "strike_step": float(
                data["strike_step"]
            ),

            "options": options,
        }

        return snapshot

    # --------------------------------------------------------
    # PARSE SNAPSHOT TIMESTAMP
    # --------------------------------------------------------

    def _parse_timestamp(
        self,
        value,
    ):
        """
        Convert a snapshot timestamp into a timezone-aware
        datetime.

        Returns None when the timestamp is missing,
        malformed, or timezone-naive.
        """

        if not value:
            return None

        try:
            if isinstance(
                value,
                datetime,
            ):
                timestamp = value
            else:
                timestamp = (
                    datetime.fromisoformat(
                        str(value)
                    )
                )
        except (
            TypeError,
            ValueError,
        ):
            return None

        # Snapshot timestamps must contain timezone data.
        if (
            timestamp.tzinfo is None
            or timestamp.utcoffset() is None
        ):
            return None

        return timestamp

    # --------------------------------------------------------
    # CHECK MARKET SESSION
    # --------------------------------------------------------

    def _is_regular_market_session(
        self,
        timestamp,
    ):
        """
        Check whether a timestamp falls inside the regular
        NSE session.

        This checks weekday + clock time.

        Exchange holidays will later be handled by the
        trading-calendar / session engine.
        """

        # Monday = 0
        # Friday = 4
        if timestamp.weekday() > 4:
            return False

        session_time = (
            timestamp.time()
            .replace(
                tzinfo=None
            )
        )

        return (
            self.market_open_time
            <= session_time
            <= self.market_close_time
        )

    # --------------------------------------------------------
    # VALIDATE SNAPSHOT PAIR
    # --------------------------------------------------------

    def validate_snapshot_pair(
        self,
        previous_snapshot,
        current_snapshot,
    ):
        """
        Ensure T1 and T2 are safe for intraday OI comparison.

        Validation includes:
        - Previous snapshot exists
        - Same expiry
        - Non-empty contracts
        - Common contracts
        - Valid timezone-aware timestamps
        - T2 newer than T1
        - Same trading date
        - Both timestamps inside regular NSE session
        - Minimum T1 -> T2 interval
        - Maximum T1 -> T2 interval
        """

        # ----------------------------------------------------
        # PREVIOUS SNAPSHOT EXISTS
        # ----------------------------------------------------

        if not previous_snapshot:
            return {
                "valid": False,
                "status": "NO_PREVIOUS_SNAPSHOT",
                "reason": (
                    "No previous option snapshot exists yet"
                ),
            }

        # ----------------------------------------------------
        # EXPIRY VALIDATION
        # ----------------------------------------------------

        previous_expiry = str(
            previous_snapshot.get(
                "expiry",
                ""
            )
        )

        current_expiry = str(
            current_snapshot.get(
                "expiry",
                ""
            )
        )

        if (
            not previous_expiry
            or not current_expiry
        ):
            return {
                "valid": False,
                "status": "MISSING_EXPIRY",
                "reason": (
                    "Snapshot expiry information is missing"
                ),
            }

        if previous_expiry != current_expiry:
            return {
                "valid": False,
                "status": "EXPIRY_CHANGED",
                "reason": (
                    "Previous and current snapshots "
                    "belong to different expiries"
                ),
            }

        # ----------------------------------------------------
        # OPTIONS VALIDATION
        # ----------------------------------------------------

        previous_options = (
            previous_snapshot.get(
                "options",
                []
            )
        )

        current_options = (
            current_snapshot.get(
                "options",
                []
            )
        )

        if (
            not previous_options
            or not current_options
        ):
            return {
                "valid": False,
                "status": "EMPTY_OPTIONS",
                "reason": (
                    "One or both snapshots contain "
                    "no option contracts"
                ),
            }

        # ----------------------------------------------------
        # TIMESTAMP VALIDATION
        # ----------------------------------------------------

        previous_timestamp = (
            self._parse_timestamp(
                previous_snapshot.get(
                    "timestamp"
                )
            )
        )

        current_timestamp = (
            self._parse_timestamp(
                current_snapshot.get(
                    "timestamp"
                )
            )
        )

        if previous_timestamp is None:
            return {
                "valid": False,
                "status": "INVALID_PREVIOUS_TIMESTAMP",
                "reason": (
                    "Previous snapshot timestamp is missing, "
                    "invalid, or timezone-naive"
                ),
            }

        if current_timestamp is None:
            return {
                "valid": False,
                "status": "INVALID_CURRENT_TIMESTAMP",
                "reason": (
                    "Current snapshot timestamp is missing, "
                    "invalid, or timezone-naive"
                ),
            }

        # ----------------------------------------------------
        # T2 MUST BE NEWER THAN T1
        # ----------------------------------------------------

        if current_timestamp <= previous_timestamp:

            if current_timestamp == previous_timestamp:
                status = "DUPLICATE_TIMESTAMP"
                reason = (
                    "Current snapshot has the same timestamp "
                    "as the previous snapshot"
                )
            else:
                status = "NON_SEQUENTIAL_TIMESTAMP"
                reason = (
                    "Current snapshot is older than "
                    "the previous snapshot"
                )

            return {
                "valid": False,
                "status": status,
                "reason": reason,
            }

        # ----------------------------------------------------
        # SAME TRADING DATE
        # ----------------------------------------------------

        if (
            previous_timestamp.date()
            != current_timestamp.date()
        ):
            return {
                "valid": False,
                "status": "TRADING_DATE_CHANGED",
                "reason": (
                    "Previous and current snapshots belong "
                    "to different trading dates"
                ),
            }

        # ----------------------------------------------------
        # REGULAR NSE SESSION
        # ----------------------------------------------------

        if not self._is_regular_market_session(
            previous_timestamp
        ):
            return {
                "valid": False,
                "status": "PREVIOUS_OUTSIDE_MARKET_SESSION",
                "reason": (
                    "Previous snapshot is outside the "
                    "regular NSE trading session"
                ),
            }

        if not self._is_regular_market_session(
            current_timestamp
        ):
            return {
                "valid": False,
                "status": "CURRENT_OUTSIDE_MARKET_SESSION",
                "reason": (
                    "Current snapshot is outside the "
                    "regular NSE trading session"
                ),
            }

        # ----------------------------------------------------
        # SNAPSHOT TIME GAP
        # ----------------------------------------------------

        gap_minutes = (
            current_timestamp
            - previous_timestamp
        ).total_seconds() / 60.0

        if (
            gap_minutes
            < self.min_snapshot_gap_minutes
        ):
            return {
                "valid": False,
                "status": "SNAPSHOT_TOO_CLOSE",
                "reason": (
                    "Snapshot interval is too short for "
                    "reliable OI comparison"
                ),
                "gap_minutes": round(
                    gap_minutes,
                    2,
                ),
            }

        if (
            gap_minutes
            > self.max_snapshot_gap_minutes
        ):
            return {
                "valid": False,
                "status": "SNAPSHOT_GAP_TOO_LARGE",
                "reason": (
                    "Snapshot interval is too large for "
                    "current intraday OI interpretation"
                ),
                "gap_minutes": round(
                    gap_minutes,
                    2,
                ),
            }

        # ----------------------------------------------------
        # COMMON CONTRACT VALIDATION
        # ----------------------------------------------------

        try:
            previous_contracts = {
                (
                    float(
                        option["strike"]
                    ),
                    str(
                        option["option_type"]
                    ).upper(),
                )
                for option in previous_options
            }

            current_contracts = {
                (
                    float(
                        option["strike"]
                    ),
                    str(
                        option["option_type"]
                    ).upper(),
                )
                for option in current_options
            }

        except (
            KeyError,
            TypeError,
            ValueError,
        ):
            return {
                "valid": False,
                "status": "INVALID_CONTRACT_DATA",
                "reason": (
                    "One or more option contracts contain "
                    "invalid strike or option-type data"
                ),
            }

        common_contracts = (
            previous_contracts
            & current_contracts
        )

        if not common_contracts:
            return {
                "valid": False,
                "status": "NO_COMMON_CONTRACTS",
                "reason": (
                    "Previous and current snapshots "
                    "have no matching contracts"
                ),
            }

        # ----------------------------------------------------
        # VALID
        # ----------------------------------------------------

        return {
            "valid": True,
            "status": "VALID",
            "reason": (
                "Snapshots are compatible for "
                "intraday OI comparison"
            ),
            "common_contracts": len(
                common_contracts
            ),
            "gap_minutes": round(
                gap_minutes,
                2,
            ),
        }

    # --------------------------------------------------------
    # SAVE CURRENT SNAPSHOT
    # --------------------------------------------------------

    def save_current_snapshot(
        self,
        current_snapshot,
    ):
        """
        Save T2 so it becomes T1 for the next cycle.

        Preserve the exact timestamp used by the live
        validation layer.
        """

        return self.snapshot_engine.save_snapshot(
            spot=current_snapshot[
                "spot"
            ],

            expiry=current_snapshot[
                "expiry"
            ],

            options=current_snapshot[
                "options"
            ],

            timestamp=current_snapshot[
                "timestamp"
            ],

            atm=current_snapshot.get(
                "atm"
            ),

            strike_step=current_snapshot.get(
                "strike_step"
            ),
        )

    # --------------------------------------------------------
    # RUN ONE OI CYCLE
    # --------------------------------------------------------

    def run_cycle(self):
        """
        Run one complete OI analysis cycle.

        First run:
            Save baseline snapshot only.

        Invalid pair:
            Save current snapshot as a new baseline and
            refuse OI interpretation.

        Valid later run:
            Compare previous vs current,
            analyze OI structure,
            then save current snapshot.
        """

        previous_snapshot = (
            self.snapshot_engine
            .load_snapshot()
        )

        current_snapshot = (
            self.fetch_current_snapshot()
        )

        validation = (
            self.validate_snapshot_pair(
                previous_snapshot,
                current_snapshot,
            )
        )

        # ----------------------------------------------------
        # FIRST SNAPSHOT / INVALID COMPARISON
        # ----------------------------------------------------

        if not validation["valid"]:

            self.save_current_snapshot(
                current_snapshot
            )

            return {
                "status": validation[
                    "status"
                ],

                "analysis_ready": False,

                "reason": validation[
                    "reason"
                ],

                "spot": current_snapshot[
                    "spot"
                ],

                "expiry": current_snapshot[
                    "expiry"
                ],

                "atm": current_snapshot[
                    "atm"
                ],

                "contracts": len(
                    current_snapshot[
                        "options"
                    ]
                ),

                "gap_minutes": validation.get(
                    "gap_minutes"
                ),

                "comparisons": [],

                "chain_analysis": None,
            }

        # ----------------------------------------------------
        # COMPARE T1 → T2
        # ----------------------------------------------------

        comparisons = (
            self.snapshot_engine
            .compare_snapshots(
                previous_snapshot,
                current_snapshot,
                self.oi_engine,
            )
        )

        if not comparisons:

            self.save_current_snapshot(
                current_snapshot
            )

            return {
                "status": "NO_VALID_COMPARISONS",

                "analysis_ready": False,

                "reason": (
                    "No valid option contracts could "
                    "be compared"
                ),

                "spot": current_snapshot[
                    "spot"
                ],

                "expiry": current_snapshot[
                    "expiry"
                ],

                "atm": current_snapshot[
                    "atm"
                ],

                "contracts": len(
                    current_snapshot[
                        "options"
                    ]
                ),

                "gap_minutes": validation.get(
                    "gap_minutes"
                ),

                "comparisons": [],

                "chain_analysis": None,
            }

        # ----------------------------------------------------
        # WHOLE-CHAIN ANALYSIS
        # ----------------------------------------------------

        chain_analysis = (
            self.oi_engine
            .analyze_chain(
                comparisons
            )
        )

        # ----------------------------------------------------
        # SAVE T2 AS NEXT T1
        # ----------------------------------------------------

        self.save_current_snapshot(
            current_snapshot
        )

        return {
            "status": "ANALYSIS_COMPLETE",

            "analysis_ready": True,

            "reason": (
                "OI comparison and whole-chain "
                "analysis completed"
            ),

            "spot": current_snapshot[
                "spot"
            ],

            "expiry": current_snapshot[
                "expiry"
            ],

            "atm": current_snapshot[
                "atm"
            ],

            "contracts": len(
                current_snapshot[
                    "options"
                ]
            ),

            "common_contracts": validation[
                "common_contracts"
            ],

            "gap_minutes": validation[
                "gap_minutes"
            ],

            "comparisons": comparisons,

            "chain_analysis": chain_analysis,
        }