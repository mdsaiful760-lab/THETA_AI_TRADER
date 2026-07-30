# ============================================================
# THETA AI TRADER — LIVE OI CYCLE ENGINE
# ============================================================

from datetime import datetime

from market_data_engine import MarketDataEngine
from option_snapshot_engine import OptionSnapshotEngine
from oi_engine import OIEngine


class LiveOIEngine:
    """
    Coordinates the live Open Interest analysis cycle.

    Flow:
    1. Fetch current NIFTY option-chain snapshot
    2. Load previous stored snapshot
    3. Validate snapshot compatibility
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

        if self.strike_range <= 0:
            raise ValueError(
                "strike_range must be greater than zero"
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
    # VALIDATE SNAPSHOT PAIR
    # --------------------------------------------------------

    def validate_snapshot_pair(
        self,
        previous_snapshot,
        current_snapshot,
    ):
        """
        Ensure T1 and T2 are suitable for comparison.
        """

        if not previous_snapshot:
            return {
                "valid": False,
                "status": "NO_PREVIOUS_SNAPSHOT",
                "reason": (
                    "No previous option snapshot exists yet"
                ),
            }

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

        previous_contracts = {
            (
                float(option["strike"]),
                str(
                    option["option_type"]
                ).upper(),
            )
            for option in previous_options
        }

        current_contracts = {
            (
                float(option["strike"]),
                str(
                    option["option_type"]
                ).upper(),
            )
            for option in current_options
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

        return {
            "valid": True,
            "status": "VALID",
            "reason": (
                "Snapshots are compatible for "
                "OI comparison"
            ),
            "common_contracts": len(
                common_contracts
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
        """

        return self.snapshot_engine.save_snapshot(
            spot=current_snapshot["spot"],
            expiry=current_snapshot["expiry"],
            options=current_snapshot["options"],
        )

    # --------------------------------------------------------
    # RUN ONE OI CYCLE
    # --------------------------------------------------------

    def run_cycle(self):
        """
        Run one complete OI analysis cycle.

        First run:
            Save baseline snapshot only.

        Later runs:
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
            "comparisons": comparisons,
            "chain_analysis": chain_analysis,
        }