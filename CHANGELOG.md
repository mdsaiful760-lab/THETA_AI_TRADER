# THETA AI TRADER - CHANGELOG

All notable changes to this project will be documented here.

---

## Version 0.3.0 (Development)

### Added
- Professional project folder structure
- Global configuration package (`configs`)
- VERSION file
- Execution Quality Gate
- Target Delta Selection
- Greeks Engine
- Liquidity Filter
- Home page market index strip integration via `DashboardFacade` (NIFTY, BANKNIFTY, SENSEX, INDIA VIX) with 1s autorefresh and offline placeholders
- Strategy Monitor integration via `DashboardFacade` (regime, active strategy, confidence, evaluation time, and four-strategy score table)
- Paper Trading dashboard integration via `DashboardFacade` (capital KPIs, positions, order counts, 1s autorefresh)
- Dashboard live integration adapter (`dashboard/live_session_adapter.py`) mapping streaming/websocket/paper/strategy handles into `DashboardFacade` soft-reads while preserving offline default
- Market page integration via `DashboardFacade` (live index cards, regime, statistics, snapshot, TradingView placeholder)

### Changed
- Improved project architecture
- Standardized project layout

### Planned
- Execution Manager
- Retry Engine
- Risk Manager
- Position Sizing Engine