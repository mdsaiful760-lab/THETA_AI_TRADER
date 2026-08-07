"""Single-startup entrypoint for THETA AI TRADER.

Running ``python main.py`` wires the complete paper-trading runtime
(Zerodha login/session restore, WebSocket, MarketDataBridge,
MarketDataStreamingEngine, AIDecisionLoop, PaperTradingRuntime, dashboard
live-handle registration — see ``system.live_runtime_bootstrap``) and then
serves the Streamlit dashboard in this SAME process, so the dashboard's
live-handle registry (a process-global in
``dashboard.live_session_adapter``) actually sees the runtime this script
just started. Running the dashboard as a separate ``streamlit run``
subprocess would not see it — Streamlit needs to run in-process for the
"no manual wiring" requirement to hold.
"""

from __future__ import annotations

import logging
import os
import sys

from dotenv import load_dotenv

from system.live_runtime_bootstrap import bootstrap_live_runtime

_LOGGER = logging.getLogger("main")

_REPO_ROOT = os.path.dirname(os.path.abspath(__file__))
_DASHBOARD_SCRIPT = os.path.join(_REPO_ROOT, "dashboard", "app.py")
_ENV_FILE = os.path.join(_REPO_ROOT, ".env")


def _configure_logging() -> None:
    """Configure root logging once for the whole process."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )


def _load_env_file() -> None:
    """Load the repository's .env into os.environ, exactly once, before bootstrap.

    KiteAuthenticator (broker/kite_authentication.py) resolves credentials
    from ``os.environ`` only — it never reads ``.env`` itself. Without this,
    KITE_API_KEY/KITE_API_SECRET/KITE_ACCESS_TOKEN are never visible to it
    even though they exist on disk. Uses the project's existing
    python-dotenv dependency; does not touch KiteAuthenticator's credential
    resolution logic. Real exported environment variables still take
    precedence (``override=False``, python-dotenv's default).
    """
    load_dotenv(_ENV_FILE)


def _run_dashboard_in_process() -> None:
    """Serve dashboard/app.py in this process via Streamlit's own bootstrap API.

    Mirrors exactly what the ``streamlit run`` CLI does internally
    (``streamlit.web.cli._main_run``): load config flags, run the
    (silent, non-blocking) credentials/telemetry check, then hand off to
    ``streamlit.web.bootstrap.run``. ``server.headless=True`` guarantees
    this never waits on a terminal prompt.
    """
    import streamlit.web.cli as st_cli
    from streamlit.web import bootstrap as st_bootstrap

    flag_options = {"server.headless": True, "browser.gatherUsageStats": False}
    st_bootstrap.load_config_options(flag_options=flag_options)
    st_cli.check_credentials()
    st_bootstrap.run(_DASHBOARD_SCRIPT, False, [], flag_options)


def main() -> int:
    """Run the full single-startup sequence, then serve the dashboard."""
    _configure_logging()
    _load_env_file()
    print("=" * 70)
    print("THETA AI TRADER — single-startup paper trading runtime")
    print("=" * 70)

    handles = bootstrap_live_runtime()

    print("\nStartup verification:")
    for line in handles.status.summary_lines():
        print(f"  {line}")
    print(f"\nDashboard starting at http://localhost:8501 (Ctrl+C to stop)\n")

    try:
        _run_dashboard_in_process()
    except KeyboardInterrupt:
        print("\nShutting down...")
    finally:
        handles.stop()
    return 0


if __name__ == "__main__":
    sys.exit(main())
