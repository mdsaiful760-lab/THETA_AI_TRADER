# ============================================================
# THETA AI TRADER — OPTION SNAPSHOT ENGINE
# ============================================================

import json
import os
from datetime import datetime


class OptionSnapshotEngine:
    """
    Stores, loads, and compares option-chain snapshots.

    Each snapshot can contain:
    - Timestamp
    - Expiry
    - Spot price
    - Strike
    - Option type
    - Option premium
    - Open Interest

    This engine does NOT:
    - Generate trading signals
    - Select strategies
    - Place orders
    """

    def __init__(
        self,
        snapshot_file="logs/option_snapshot.json",
    ):
        self.snapshot_file = snapshot_file

    # --------------------------------------------------------
    # ENSURE STORAGE DIRECTORY
    # --------------------------------------------------------

    def ensure_directory(self):
        """
        Create snapshot directory if it does not exist.
        """

        directory = os.path.dirname(
            self.snapshot_file
        )

        if directory:
            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------------------------------
    # SAVE SNAPSHOT
    # --------------------------------------------------------

    def save_snapshot(
        self,
        spot,
        expiry,
        options,
    ):
        """
        Save the latest option-chain snapshot.

        options must be a list of dictionaries.
        """

        if not options:
            raise ValueError(
                "Option snapshot cannot be empty"
            )

        self.ensure_directory()

        snapshot = {
            "timestamp": (
                datetime.now()
                .astimezone()
                .isoformat()
            ),
            "spot": float(spot),
            "expiry": str(expiry),
            "options": options,
        }

        with open(
            self.snapshot_file,
            "w",
            encoding="utf-8",
        ) as file:

            json.dump(
                snapshot,
                file,
                indent=2,
            )

        return snapshot

    # --------------------------------------------------------
    # LOAD PREVIOUS SNAPSHOT
    # --------------------------------------------------------

    def load_snapshot(self):
        """
        Load the most recently stored option snapshot.
        """

        if not os.path.exists(
            self.snapshot_file
        ):
            return None

        with open(
            self.snapshot_file,
            "r",
            encoding="utf-8",
        ) as file:

            snapshot = json.load(
                file
            )

        return snapshot

    # --------------------------------------------------------
    # COMPARE OPTION SNAPSHOTS
    # --------------------------------------------------------

    def compare_snapshots(
        self,
        previous_snapshot,
        current_snapshot,
        oi_engine,
    ):
        """
        Compare two option-chain snapshots.

        Matches contracts using:
        - Strike
        - Option type

        Calculates:
        - Price change %
        - Absolute OI change
        - OI change %
        - OI activity classification
        - Support/resistance interpretation
        """

        if not previous_snapshot:
            raise ValueError(
                "Previous snapshot is required"
            )

        if not current_snapshot:
            raise ValueError(
                "Current snapshot is required"
            )

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

        previous_map = {}

        # ----------------------------------------------------
        # BUILD PREVIOUS CONTRACT MAP
        # ----------------------------------------------------

        for option in previous_options:

            key = (
                float(
                    option["strike"]
                ),
                str(
                    option["option_type"]
                ).upper(),
            )

            previous_map[key] = option

        comparisons = []

        # ----------------------------------------------------
        # COMPARE CURRENT CONTRACTS
        # ----------------------------------------------------

        for current in current_options:

            key = (
                float(
                    current["strike"]
                ),
                str(
                    current["option_type"]
                ).upper(),
            )

            previous = previous_map.get(
                key
            )

            if not previous:
                continue

            previous_price = float(
                previous.get(
                    "price",
                    0,
                ) or 0
            )

            current_price = float(
                current.get(
                    "price",
                    0,
                ) or 0
            )

            previous_oi = float(
                previous.get(
                    "oi",
                    0,
                ) or 0
            )

            current_oi = float(
                current.get(
                    "oi",
                    0,
                ) or 0
            )

            # ------------------------------------------------
            # DATA VALIDATION
            # ------------------------------------------------

            # Percentage change cannot safely be calculated
            # when the previous price or previous OI is zero.
            if (
                previous_price <= 0
                or previous_oi <= 0
            ):
                continue

            # ------------------------------------------------
            # ABSOLUTE OI CHANGE
            # ------------------------------------------------

            oi_change = (
                current_oi
                - previous_oi
            )

            # ------------------------------------------------
            # CLASSIFY PRICE + OI ACTIVITY
            # ------------------------------------------------

            activity = oi_engine.classify(
                previous_price=previous_price,
                current_price=current_price,
                previous_oi=previous_oi,
                current_oi=current_oi,
            )

            # ------------------------------------------------
            # INTERPRET CE / PE ACTIVITY
            # ------------------------------------------------

            interpretation = (
                oi_engine.interpret_option_activity(
                    option_type=key[1],
                    classification=activity[
                        "classification"
                    ],
                )
            )

            # ------------------------------------------------
            # BUILD NORMALIZED COMPARISON
            # ------------------------------------------------

            comparisons.append({
                "strike": key[0],

                "option_type": key[1],

                "symbol": current.get(
                    "symbol"
                ),

                "previous_price": (
                    previous_price
                ),

                "current_price": (
                    current_price
                ),

                "price_change_pct": activity[
                    "price_change_pct"
                ],

                "previous_oi": (
                    previous_oi
                ),

                "current_oi": (
                    current_oi
                ),

                # Absolute OI contracts added/removed
                "oi_change": (
                    oi_change
                ),

                # Percentage OI change
                "oi_change_pct": activity[
                    "oi_change_pct"
                ],

                "classification": activity[
                    "classification"
                ],

                "interpretation": interpretation[
                    "interpretation"
                ],
            })

        return comparisons