# ============================================================
# THETA AI TRADER — CENTRAL CONFIGURATION MANAGER
# ============================================================

import copy
import json
import os
from datetime import datetime


class ConfigManager:
    """
    Central configuration manager for THETA AI TRADER.

    Purpose:
    - Keep configurable values outside trading engines
    - Allow future dashboard/frontend configuration
    - Validate all configuration changes
    - Persist configuration across application restarts
    - Maintain configuration version numbers
    - Maintain configuration change history
    - Provide configuration snapshots to backend engines
    - Protect critical system settings

    Architecture:

        Dashboard
            |
            v
        ConfigManager
            |
            +--> RiskManagementEngine
            +--> PositionSizingEngine
            +--> SignalDecisionEngine
            +--> MarketRegimeEngine
            +--> LiveOIEngine
            +--> Future ExecutionEngine

    IMPORTANT:

    ConfigManager stores USER CONFIGURATION.

    Runtime emergency states such as broker failures,
    execution locks and automatic kill switches should
    eventually live in a separate runtime safety-state
    manager.

    This class does NOT:
    - Generate trading signals
    - Place orders
    - Modify positions
    - Connect to Zerodha
    """

    # --------------------------------------------------------
    # DEFAULT CONFIGURATION
    # --------------------------------------------------------

    DEFAULT_CONFIG = {

        "risk": {

            "max_risk_per_trade_pct": 1.0,

            "max_daily_loss_pct": 3.0,

            "max_account_drawdown_pct": 10.0,

            "max_consecutive_losses": 3,

            "max_open_positions": 3,

            "caution_risk_multiplier": 0.50,

            "expiry_risk_multiplier": 0.50,

            "medium_confidence_multiplier": 0.75,

            "minimum_risk_multiplier": 0.25,
        },

        # ----------------------------------------------------
        # POSITION SIZING
        # ----------------------------------------------------

        "position_sizing": {

            # Maximum lots allowed for one normal trade.
            "max_lots_per_trade": 10,

            # Additional safety restriction for expiry day.
            # Effective expiry limit will later be:
            #
            # min(
            #     max_lots_per_trade,
            #     expiry_max_lots_per_trade
            # )
            "expiry_max_lots_per_trade": 5,
        },

        "trading": {

            "trading_enabled": True,

            "new_entries_enabled": True,

            "expiry_trading_enabled": True,
        },

        "signal": {

            "minimum_confidence": "MEDIUM",

            "allow_caution_signals": True,
        },

        "oi": {

            "strike_range": 10,

            "min_snapshot_gap_minutes": 1.0,

            "max_snapshot_gap_minutes": 15.0,
        },

        "system": {

            "environment": "PAPER",

            "config_version": 1,

            "last_updated": None,
        },
    }

    # --------------------------------------------------------
    # USER-EDITABLE SETTINGS
    # --------------------------------------------------------

    EDITABLE_SETTINGS = {

        "risk": {
            "max_risk_per_trade_pct",
            "max_daily_loss_pct",
            "max_account_drawdown_pct",
            "max_consecutive_losses",
            "max_open_positions",
            "caution_risk_multiplier",
            "expiry_risk_multiplier",
            "medium_confidence_multiplier",
            "minimum_risk_multiplier",
        },

        "position_sizing": {
            "max_lots_per_trade",
            "expiry_max_lots_per_trade",
        },

        "trading": {
            "trading_enabled",
            "new_entries_enabled",
            "expiry_trading_enabled",
        },

        "signal": {
            "minimum_confidence",
            "allow_caution_signals",
        },

        "oi": {
            "strike_range",
            "min_snapshot_gap_minutes",
            "max_snapshot_gap_minutes",
        },

        "system": {
            "environment",
        },
    }

    # --------------------------------------------------------
    # INITIALIZATION
    # --------------------------------------------------------

    def __init__(
        self,
        config_file="config/user_config.json",
        history_file="logs/config_history.jsonl",
    ):

        self.config_file = str(
            config_file
        )

        self.history_file = str(
            history_file
        )

        self.config = {}

        self.load()

    # --------------------------------------------------------
    # TIMESTAMP
    # --------------------------------------------------------

    def _timestamp(self):
        """
        Return timezone-aware timestamp.
        """

        return (
            datetime.now()
            .astimezone()
            .isoformat()
        )

    # --------------------------------------------------------
    # ENSURE DIRECTORY
    # --------------------------------------------------------

    def _ensure_parent_directory(
        self,
        file_path,
    ):
        """
        Create parent directory when required.
        """

        directory = os.path.dirname(
            file_path
        )

        if directory:

            os.makedirs(
                directory,
                exist_ok=True,
            )

    # --------------------------------------------------------
    # SAFE COPY
    # --------------------------------------------------------

    def _copy(
        self,
        value,
    ):
        """
        Return deep copy so callers cannot accidentally
        mutate internal configuration.
        """

        return copy.deepcopy(
            value
        )

    # --------------------------------------------------------
    # MERGE CONFIGURATION
    # --------------------------------------------------------

    def _merge_with_defaults(
        self,
        stored_config,
    ):
        """
        Merge stored configuration with current defaults.

        This allows us to add new configuration fields in
        future software versions without destroying older
        user settings.

        Example:

        An older user_config.json may not contain the
        position_sizing section.

        The new defaults will automatically add it while
        preserving all existing user settings.
        """

        merged = self._copy(
            self.DEFAULT_CONFIG
        )

        if not isinstance(
            stored_config,
            dict,
        ):
            return merged

        for section, values in stored_config.items():

            if (
                section not in merged
                or not isinstance(
                    values,
                    dict,
                )
            ):
                continue

            for key, value in values.items():

                if key in merged[section]:

                    merged[
                        section
                    ][
                        key
                    ] = value

        return merged

    # --------------------------------------------------------
    # LOAD CONFIGURATION
    # --------------------------------------------------------

    def load(self):
        """
        Load persistent configuration.

        If no configuration exists, create defaults.
        """

        if not os.path.exists(
            self.config_file
        ):

            self.config = self._copy(
                self.DEFAULT_CONFIG
            )

            self.config[
                "system"
            ][
                "last_updated"
            ] = self._timestamp()

            self.save(
                increment_version=False
            )

            return self.get_all()

        try:

            with open(
                self.config_file,
                "r",
                encoding="utf-8",
            ) as file:

                stored = json.load(
                    file
                )

        except (
            json.JSONDecodeError,
            OSError,
        ) as error:

            raise RuntimeError(
                "Unable to load configuration file: "
                f"{error}"
            )

        self.config = (
            self._merge_with_defaults(
                stored
            )
        )

        self.validate_all()

        return self.get_all()

    # --------------------------------------------------------
    # SAVE CONFIGURATION
    # --------------------------------------------------------

    def save(
        self,
        increment_version=False,
    ):
        """
        Persist configuration atomically.

        Atomic replacement reduces the chance of leaving
        a partially-written configuration file.
        """

        self.validate_all()

        if increment_version:

            current_version = int(
                self.config[
                    "system"
                ].get(
                    "config_version",
                    1,
                )
            )

            self.config[
                "system"
            ][
                "config_version"
            ] = (
                current_version
                + 1
            )

        self.config[
            "system"
        ][
            "last_updated"
        ] = self._timestamp()

        self._ensure_parent_directory(
            self.config_file
        )

        temporary_file = (
            self.config_file
            + ".tmp"
        )

        try:

            with open(
                temporary_file,
                "w",
                encoding="utf-8",
            ) as file:

                json.dump(
                    self.config,
                    file,
                    indent=2,
                    sort_keys=True,
                )

                file.flush()

                os.fsync(
                    file.fileno()
                )

            os.replace(
                temporary_file,
                self.config_file,
            )

        finally:

            if os.path.exists(
                temporary_file
            ):

                try:

                    os.remove(
                        temporary_file
                    )

                except OSError:
                    pass

        return self.get_all()

    # --------------------------------------------------------
    # GET COMPLETE CONFIGURATION
    # --------------------------------------------------------

    def get_all(self):
        """
        Return complete configuration copy.
        """

        return self._copy(
            self.config
        )

    # --------------------------------------------------------
    # GET SECTION
    # --------------------------------------------------------

    def get_section(
        self,
        section,
    ):
        """
        Return one configuration section.
        """

        section = str(
            section
        ).lower()

        if section not in self.config:

            raise KeyError(
                f"Unknown configuration section: {section}"
            )

        return self._copy(
            self.config[
                section
            ]
        )

    # --------------------------------------------------------
    # GET SETTING
    # --------------------------------------------------------

    def get_setting(
        self,
        section,
        key,
    ):
        """
        Return one configuration value.
        """

        section = str(
            section
        ).lower()

        key = str(
            key
        )

        if section not in self.config:

            raise KeyError(
                f"Unknown configuration section: {section}"
            )

        if key not in self.config[
            section
        ]:

            raise KeyError(
                f"Unknown configuration setting: "
                f"{section}.{key}"
            )

        return self._copy(
            self.config[
                section
            ][
                key
            ]
        )

    # --------------------------------------------------------
    # EDITABLE CHECK
    # --------------------------------------------------------

    def _ensure_editable(
        self,
        section,
        key,
    ):
        """
        Ensure frontend/user is allowed to modify setting.
        """

        if section not in self.EDITABLE_SETTINGS:

            raise PermissionError(
                f"Configuration section is protected: "
                f"{section}"
            )

        if (
            key
            not in self.EDITABLE_SETTINGS[
                section
            ]
        ):

            raise PermissionError(
                f"Configuration setting is protected: "
                f"{section}.{key}"
            )

    # --------------------------------------------------------
    # BOOLEAN VALIDATION
    # --------------------------------------------------------

    def _validate_boolean(
        self,
        value,
        name,
    ):
        """
        Require actual boolean values.

        Prevents strings such as 'False' from accidentally
        behaving as True in Python.
        """

        if not isinstance(
            value,
            bool,
        ):

            raise ValueError(
                f"{name} must be True or False"
            )

        return value

    # --------------------------------------------------------
    # NUMBER VALIDATION
    # --------------------------------------------------------

    def _validate_number(
        self,
        value,
        name,
        minimum=None,
        maximum=None,
    ):
        """
        Validate numeric configuration.
        """

        if isinstance(
            value,
            bool,
        ):

            raise ValueError(
                f"{name} must be numeric"
            )

        try:

            value = float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            raise ValueError(
                f"{name} must be numeric"
            )

        if (
            minimum is not None
            and value < minimum
        ):

            raise ValueError(
                f"{name} cannot be below "
                f"{minimum}"
            )

        if (
            maximum is not None
            and value > maximum
        ):

            raise ValueError(
                f"{name} cannot exceed "
                f"{maximum}"
            )

        return value

    # --------------------------------------------------------
    # INTEGER VALIDATION
    # --------------------------------------------------------

    def _validate_integer(
        self,
        value,
        name,
        minimum=None,
        maximum=None,
    ):
        """
        Validate integer configuration.
        """

        if isinstance(
            value,
            bool,
        ):

            raise ValueError(
                f"{name} must be an integer"
            )

        try:

            numeric_value = float(
                value
            )

        except (
            TypeError,
            ValueError,
        ):

            raise ValueError(
                f"{name} must be an integer"
            )

        if not numeric_value.is_integer():

            raise ValueError(
                f"{name} must be an integer"
            )

        value = int(
            numeric_value
        )

        if (
            minimum is not None
            and value < minimum
        ):

            raise ValueError(
                f"{name} cannot be below "
                f"{minimum}"
            )

        if (
            maximum is not None
            and value > maximum
        ):

            raise ValueError(
                f"{name} cannot exceed "
                f"{maximum}"
            )

        return value

    # --------------------------------------------------------
    # SETTING VALIDATION
    # --------------------------------------------------------

    def validate_setting(
        self,
        section,
        key,
        value,
    ):
        """
        Validate and normalize one setting.

        These ranges are software guardrails.

        They can be changed deliberately in code later,
        but arbitrary frontend input cannot bypass them.
        """

        section = str(
            section
        ).lower()

        key = str(
            key
        )

        name = (
            f"{section}.{key}"
        )

        # ----------------------------------------------------
        # RISK SETTINGS
        # ----------------------------------------------------

        if section == "risk":

            if key == "max_risk_per_trade_pct":

                return self._validate_number(
                    value,
                    name,
                    minimum=0.10,
                    maximum=5.0,
                )

            if key == "max_daily_loss_pct":

                return self._validate_number(
                    value,
                    name,
                    minimum=0.25,
                    maximum=15.0,
                )

            if key == "max_account_drawdown_pct":

                return self._validate_number(
                    value,
                    name,
                    minimum=1.0,
                    maximum=50.0,
                )

            if key == "max_consecutive_losses":

                return self._validate_integer(
                    value,
                    name,
                    minimum=1,
                    maximum=20,
                )

            if key == "max_open_positions":

                return self._validate_integer(
                    value,
                    name,
                    minimum=1,
                    maximum=20,
                )

            if key in (
                "caution_risk_multiplier",
                "expiry_risk_multiplier",
                "medium_confidence_multiplier",
                "minimum_risk_multiplier",
            ):

                return self._validate_number(
                    value,
                    name,
                    minimum=0.10,
                    maximum=1.0,
                )

        # ----------------------------------------------------
        # POSITION SIZING SETTINGS
        # ----------------------------------------------------

        if section == "position_sizing":

            if key == "max_lots_per_trade":

                return self._validate_integer(
                    value,
                    name,
                    minimum=1,
                    maximum=1000,
                )

            if key == "expiry_max_lots_per_trade":

                return self._validate_integer(
                    value,
                    name,
                    minimum=1,
                    maximum=1000,
                )

        # ----------------------------------------------------
        # TRADING SETTINGS
        # ----------------------------------------------------

        if section == "trading":

            if key in (
                "trading_enabled",
                "new_entries_enabled",
                "expiry_trading_enabled",
            ):

                return self._validate_boolean(
                    value,
                    name,
                )

        # ----------------------------------------------------
        # SIGNAL SETTINGS
        # ----------------------------------------------------

        if section == "signal":

            if key == "minimum_confidence":

                normalized = str(
                    value
                ).upper()

                if normalized not in (
                    "LOW",
                    "MEDIUM",
                    "HIGH",
                ):

                    raise ValueError(
                        f"{name} must be "
                        "LOW, MEDIUM or HIGH"
                    )

                return normalized

            if key == "allow_caution_signals":

                return self._validate_boolean(
                    value,
                    name,
                )

        # ----------------------------------------------------
        # OI SETTINGS
        # ----------------------------------------------------

        if section == "oi":

            if key == "strike_range":

                return self._validate_integer(
                    value,
                    name,
                    minimum=1,
                    maximum=50,
                )

            if key == "min_snapshot_gap_minutes":

                return self._validate_number(
                    value,
                    name,
                    minimum=0.25,
                    maximum=15.0,
                )

            if key == "max_snapshot_gap_minutes":

                return self._validate_number(
                    value,
                    name,
                    minimum=1.0,
                    maximum=60.0,
                )

        # ----------------------------------------------------
        # SYSTEM SETTINGS
        # ----------------------------------------------------

        if (
            section == "system"
            and key == "environment"
        ):

            normalized = str(
                value
            ).upper()

            if normalized not in (
                "PAPER",
                "LIVE",
            ):

                raise ValueError(
                    "system.environment must be "
                    "PAPER or LIVE"
                )

            return normalized

        raise KeyError(
            f"Unknown configuration setting: "
            f"{section}.{key}"
        )

    # --------------------------------------------------------
    # CROSS-SETTING VALIDATION
    # --------------------------------------------------------

    def _validate_relationships(
        self,
        config,
    ):
        """
        Validate relationships between settings.

        Individual fields may be valid while their
        combination is not.
        """

        # ----------------------------------------------------
        # OI SNAPSHOT RELATIONSHIP
        # ----------------------------------------------------

        oi = config[
            "oi"
        ]

        minimum_gap = float(
            oi[
                "min_snapshot_gap_minutes"
            ]
        )

        maximum_gap = float(
            oi[
                "max_snapshot_gap_minutes"
            ]
        )

        if maximum_gap <= minimum_gap:

            raise ValueError(
                "oi.max_snapshot_gap_minutes must be "
                "greater than "
                "oi.min_snapshot_gap_minutes"
            )

        # ----------------------------------------------------
        # RISK MULTIPLIER RELATIONSHIPS
        # ----------------------------------------------------

        risk = config[
            "risk"
        ]

        minimum_multiplier = float(
            risk[
                "minimum_risk_multiplier"
            ]
        )

        adjustable_multipliers = (
            "caution_risk_multiplier",
            "expiry_risk_multiplier",
            "medium_confidence_multiplier",
        )

        for key in adjustable_multipliers:

            if (
                float(
                    risk[
                        key
                    ]
                )
                < minimum_multiplier
            ):

                raise ValueError(
                    f"risk.{key} cannot be below "
                    "risk.minimum_risk_multiplier"
                )

        # ----------------------------------------------------
        # POSITION SIZING RELATIONSHIP
        # ----------------------------------------------------

        position_sizing = config[
            "position_sizing"
        ]

        max_lots = int(
            position_sizing[
                "max_lots_per_trade"
            ]
        )

        expiry_max_lots = int(
            position_sizing[
                "expiry_max_lots_per_trade"
            ]
        )

        if expiry_max_lots > max_lots:

            raise ValueError(
                "position_sizing."
                "expiry_max_lots_per_trade cannot exceed "
                "position_sizing.max_lots_per_trade"
            )

    # --------------------------------------------------------
    # VALIDATE COMPLETE CONFIGURATION
    # --------------------------------------------------------

    def validate_all(self):
        """
        Validate entire active configuration.
        """

        normalized = self._copy(
            self.config
        )

        for section, keys in (
            self.EDITABLE_SETTINGS.items()
        ):

            for key in keys:

                if (
                    section not in normalized
                    or key not in normalized[
                        section
                    ]
                ):

                    raise ValueError(
                        "Missing required setting: "
                        f"{section}.{key}"
                    )

                normalized[
                    section
                ][
                    key
                ] = self.validate_setting(
                    section,
                    key,
                    normalized[
                        section
                    ][
                        key
                    ],
                )

        self._validate_relationships(
            normalized
        )

        self.config = normalized

        return True

    # --------------------------------------------------------
    # HISTORY
    # --------------------------------------------------------

    def _write_history(
        self,
        event,
    ):
        """
        Append configuration change to JSONL audit log.

        Failure to write audit history must be visible.
        """

        self._ensure_parent_directory(
            self.history_file
        )

        record = {
            "timestamp": self._timestamp(),

            **event,
        }

        try:

            with open(
                self.history_file,
                "a",
                encoding="utf-8",
            ) as file:

                file.write(
                    json.dumps(
                        record,
                        sort_keys=True,
                    )
                )

                file.write(
                    "\n"
                )

        except OSError as error:

            raise RuntimeError(
                "Configuration changed but audit "
                "history could not be written: "
                f"{error}"
            )

    # --------------------------------------------------------
    # UPDATE ONE SETTING
    # --------------------------------------------------------

    def update_setting(
        self,
        section,
        key,
        value,
        source="BACKEND",
        user_id=None,
    ):
        """
        Update one user-configurable setting.

        This is the primary method the future dashboard
        should call.

        Example:

            update_setting(
                "risk",
                "max_risk_per_trade_pct",
                0.50,
                source="DASHBOARD",
            )

        Position-sizing example:

            update_setting(
                "position_sizing",
                "max_lots_per_trade",
                5,
                source="DASHBOARD",
            )
        """

        section = str(
            section
        ).lower()

        key = str(
            key
        )

        if section not in self.config:

            raise KeyError(
                f"Unknown configuration section: {section}"
            )

        if key not in self.config[
            section
        ]:

            raise KeyError(
                f"Unknown configuration setting: "
                f"{section}.{key}"
            )

        self._ensure_editable(
            section,
            key,
        )

        normalized_value = (
            self.validate_setting(
                section,
                key,
                value,
            )
        )

        old_value = self._copy(
            self.config[
                section
            ][
                key
            ]
        )

        # No unnecessary version increase when the
        # effective setting did not change.
        if old_value == normalized_value:

            return {
                "changed": False,

                "section": section,

                "key": key,

                "old_value": old_value,

                "new_value": normalized_value,

                "config_version": (
                    self.get_setting(
                        "system",
                        "config_version",
                    )
                ),
            }

        candidate_config = self.get_all()

        candidate_config[
            section
        ][
            key
        ] = normalized_value

        # Validate relationships BEFORE modifying
        # the active configuration.
        self._validate_relationships(
            candidate_config
        )

        previous_config = self.get_all()

        try:

            self.config = candidate_config

            self.save(
                increment_version=True
            )

            version = self.get_setting(
                "system",
                "config_version",
            )

            self._write_history({
                "event": "SETTING_UPDATED",

                "source": str(
                    source
                ),

                "user_id": user_id,

                "section": section,

                "key": key,

                "old_value": old_value,

                "new_value": normalized_value,

                "config_version": version,
            })

        except Exception:

            self.config = previous_config

            raise

        return {
            "changed": True,

            "section": section,

            "key": key,

            "old_value": old_value,

            "new_value": normalized_value,

            "config_version": (
                self.get_setting(
                    "system",
                    "config_version",
                )
            ),
        }

    # --------------------------------------------------------
    # UPDATE MULTIPLE SETTINGS
    # --------------------------------------------------------

    def update_many(
        self,
        updates,
        source="BACKEND",
        user_id=None,
    ):
        """
        Atomically update multiple settings.

        Expected format:

        {
            "risk": {
                "max_risk_per_trade_pct": 0.5,
                "max_daily_loss_pct": 2.0
            },

            "position_sizing": {
                "max_lots_per_trade": 5,
                "expiry_max_lots_per_trade": 2
            },

            "trading": {
                "new_entries_enabled": True
            }
        }

        Either the complete update succeeds or none of
        the settings are changed.
        """

        if not isinstance(
            updates,
            dict,
        ):

            raise ValueError(
                "updates must be a dictionary"
            )

        candidate = self.get_all()

        changes = []

        for section, values in updates.items():

            section = str(
                section
            ).lower()

            if not isinstance(
                values,
                dict,
            ):

                raise ValueError(
                    f"{section} update must "
                    "be a dictionary"
                )

            if section not in candidate:

                raise KeyError(
                    f"Unknown configuration section: "
                    f"{section}"
                )

            for key, value in values.items():

                key = str(
                    key
                )

                if key not in candidate[
                    section
                ]:

                    raise KeyError(
                        "Unknown configuration setting: "
                        f"{section}.{key}"
                    )

                self._ensure_editable(
                    section,
                    key,
                )

                normalized_value = (
                    self.validate_setting(
                        section,
                        key,
                        value,
                    )
                )

                old_value = candidate[
                    section
                ][
                    key
                ]

                if old_value == normalized_value:
                    continue

                candidate[
                    section
                ][
                    key
                ] = normalized_value

                changes.append({
                    "section": section,

                    "key": key,

                    "old_value": old_value,

                    "new_value": (
                        normalized_value
                    ),
                })

        if not changes:

            return {
                "changed": False,

                "changes": [],

                "config_version": (
                    self.get_setting(
                        "system",
                        "config_version",
                    )
                ),
            }

        self._validate_relationships(
            candidate
        )

        previous_config = self.get_all()

        try:

            self.config = candidate

            self.save(
                increment_version=True
            )

            version = self.get_setting(
                "system",
                "config_version",
            )

            self._write_history({
                "event": "MULTIPLE_SETTINGS_UPDATED",

                "source": str(
                    source
                ),

                "user_id": user_id,

                "changes": changes,

                "config_version": version,
            })

        except Exception:

            self.config = previous_config

            raise

        return {
            "changed": True,

            "changes": changes,

            "config_version": (
                self.get_setting(
                    "system",
                    "config_version",
                )
            ),
        }

    # --------------------------------------------------------
    # RESET SECTION
    # --------------------------------------------------------

    def reset_section(
        self,
        section,
        source="BACKEND",
        user_id=None,
    ):
        """
        Reset one editable section to software defaults.
        """

        section = str(
            section
        ).lower()

        if section not in self.DEFAULT_CONFIG:

            raise KeyError(
                f"Unknown configuration section: "
                f"{section}"
            )

        if section == "system":

            raise PermissionError(
                "System section cannot be reset "
                "through user configuration"
            )

        if section not in self.EDITABLE_SETTINGS:

            raise PermissionError(
                f"Section is protected: {section}"
            )

        updates = {
            section: {}
        }

        for key in self.EDITABLE_SETTINGS[
            section
        ]:

            updates[
                section
            ][
                key
            ] = self._copy(
                self.DEFAULT_CONFIG[
                    section
                ][
                    key
                ]
            )

        return self.update_many(
            updates,
            source=source,
            user_id=user_id,
        )

    # --------------------------------------------------------
    # RESET ALL USER SETTINGS
    # --------------------------------------------------------

    def reset_all(
        self,
        source="BACKEND",
        user_id=None,
    ):
        """
        Reset all user-editable settings to defaults.

        Configuration version is preserved and incremented.
        """

        candidate = self.get_all()

        changes = []

        for section, keys in (
            self.EDITABLE_SETTINGS.items()
        ):

            # Environment is intentionally preserved
            # during general reset.
            if section == "system":
                continue

            for key in keys:

                default_value = self._copy(
                    self.DEFAULT_CONFIG[
                        section
                    ][
                        key
                    ]
                )

                old_value = candidate[
                    section
                ][
                    key
                ]

                if old_value == default_value:
                    continue

                candidate[
                    section
                ][
                    key
                ] = default_value

                changes.append({
                    "section": section,

                    "key": key,

                    "old_value": old_value,

                    "new_value": default_value,
                })

        if not changes:

            return {
                "changed": False,

                "changes": [],

                "config_version": (
                    self.get_setting(
                        "system",
                        "config_version",
                    )
                ),
            }

        self._validate_relationships(
            candidate
        )

        previous_config = self.get_all()

        try:

            self.config = candidate

            self.save(
                increment_version=True
            )

            version = self.get_setting(
                "system",
                "config_version",
            )

            self._write_history({
                "event": "CONFIG_RESET",

                "source": str(
                    source
                ),

                "user_id": user_id,

                "changes": changes,

                "config_version": version,
            })

        except Exception:

            self.config = previous_config

            raise

        return {
            "changed": True,

            "changes": changes,

            "config_version": (
                self.get_setting(
                    "system",
                    "config_version",
                )
            ),
        }

    # --------------------------------------------------------
    # CONFIGURATION SNAPSHOT
    # --------------------------------------------------------

    def create_snapshot(self):
        """
        Create immutable-style configuration snapshot.

        Future trade records should store this version so
        we know exactly which settings approved a trade.
        """

        return {
            "config_version": (
                self.get_setting(
                    "system",
                    "config_version",
                )
            ),

            "created_at": (
                self._timestamp()
            ),

            "configuration": (
                self.get_all()
            ),
        }

    # --------------------------------------------------------
    # RISK CONFIGURATION
    # --------------------------------------------------------

    def get_risk_config(self):
        """
        Convenience method for RiskManagementEngine.
        """

        return self.get_section(
            "risk"
        )

    # --------------------------------------------------------
    # POSITION SIZING CONFIGURATION
    # --------------------------------------------------------

    def get_position_sizing_config(self):
        """
        Convenience method for PositionSizingEngine.

        These settings are intended to be editable from
        the future dashboard.
        """

        return self.get_section(
            "position_sizing"
        )

    # --------------------------------------------------------
    # OI CONFIGURATION
    # --------------------------------------------------------

    def get_oi_config(self):
        """
        Convenience method for LiveOIEngine.
        """

        return self.get_section(
            "oi"
        )

    # --------------------------------------------------------
    # SIGNAL CONFIGURATION
    # --------------------------------------------------------

    def get_signal_config(self):
        """
        Convenience method for SignalDecisionEngine.
        """

        return self.get_section(
            "signal"
        )

    # --------------------------------------------------------
    # TRADING CONFIGURATION
    # --------------------------------------------------------

    def get_trading_config(self):
        """
        Convenience method for trading/session engines.
        """

        return self.get_section(
            "trading"
        )