"""Institutional CALLS | STRIKE | PUTS option chain table.

Pivots the facade's real per-option-type rows into one row per strike with
call metrics on the left and put metrics on the right, mirrored around a
central Strike column — the standard institutional option-chain layout.
Every value rendered is already-computed upstream (never derived here);
ATM and AI-selected strikes are highlighted using real facade-provided
values, never guessed.
"""

from __future__ import annotations

import html

import streamlit as st

from dashboard.view_models import PLACEHOLDER

_METRIC_COLUMNS: tuple[tuple[str, str], ...] = (
    ("oi", "OI"),
    ("oi_change", "OI Chg"),
    ("volume", "Volume"),
    ("iv", "IV"),
    ("delta", "Delta"),
    ("gamma", "Gamma"),
    ("theta", "Theta"),
    ("vega", "Vega"),
    ("bid", "Bid"),
    ("ask", "Ask"),
    ("ltp", "LTP"),
)


def _pivot_by_strike(
    columns: tuple[str, ...], rows: tuple[tuple[str, ...], ...]
) -> dict[str, dict[str, dict[str, str]]]:
    """Group real option-chain rows into ``{strike: {"CE": {...}, "PE": {...}}}``."""
    index = {name: i for i, name in enumerate(columns)}
    if "strike" not in index or "type" not in index:
        return {}
    by_strike: dict[str, dict[str, dict[str, str]]] = {}
    for row in rows:
        strike = row[index["strike"]]
        option_type = row[index["type"]].strip().upper()
        side = "CE" if option_type in ("CE", "CALL") else "PE" if option_type in ("PE", "PUT") else None
        if side is None:
            continue
        metrics = {name: row[index[name]] for name, _label in _METRIC_COLUMNS if name in index}
        by_strike.setdefault(strike, {})[side] = metrics
    return by_strike


def _sorted_strikes(by_strike: dict[str, dict[str, dict[str, str]]]) -> list[str]:
    def _key(strike: str) -> float:
        try:
            return float(strike.replace(",", ""))
        except ValueError:
            return float("inf")

    return sorted(by_strike.keys(), key=_key)


def _cell(value: str | None) -> str:
    return html.escape(value if value not in (None, "") else PLACEHOLDER)


def render_option_chain_table(
    *,
    columns: tuple[str, ...],
    rows: tuple[tuple[str, ...], ...],
    atm_strike: str,
    ai_selected_strikes: tuple[str, ...] = (),
    height: int = 600,
) -> None:
    """Render the institutional CALLS | STRIKE | PUTS option chain table.

    Args:
        columns: Real option-chain column names from the facade.
        rows: Real per-option-type option-chain rows.
        atm_strike: Real ATM strike display value (row highlight only).
        ai_selected_strikes: Real strikes chosen by the top-ranked strategy's
            own legs (row highlight only) — empty when no strategy is active.
        height: Scrollable panel height in pixels.
    """
    by_strike = _pivot_by_strike(columns, rows)
    if not by_strike:
        st.info("Option chain unavailable — awaiting backend market snapshot")
        return

    selected = {s.strip() for s in ai_selected_strikes}
    call_header = "".join(f"<th>{label}</th>" for _key, label in reversed(_METRIC_COLUMNS))
    put_header = "".join(f"<th>{label}</th>" for _key, label in _METRIC_COLUMNS)

    body_rows: list[str] = []
    for strike in _sorted_strikes(by_strike):
        sides = by_strike[strike]
        call = sides.get("CE", {})
        put = sides.get("PE", {})
        is_atm = atm_strike not in (PLACEHOLDER, "", None) and strike.strip() == atm_strike.strip()
        is_ai = strike.strip() in selected
        row_classes = " ".join(
            cls for cls, flag in (("atm-row", is_atm), ("ai-row", is_ai)) if flag
        )
        call_cells = "".join(
            f"<td>{_cell(call.get(key))}</td>" for key, _label in reversed(_METRIC_COLUMNS)
        )
        put_cells = "".join(
            f"<td>{_cell(put.get(key))}</td>" for key, _label in _METRIC_COLUMNS
        )
        badge = " <span class='theta-chain-ai-badge'>AI</span>" if is_ai else ""
        body_rows.append(
            f"<tr class='{row_classes}'>"
            f"{call_cells}"
            f"<td class='theta-chain-strike'>{_cell(strike)}{badge}</td>"
            f"{put_cells}"
            "</tr>"
        )

    table_html = f"""
    <div class='theta-chain-scroll' style='max-height:{height}px;'>
      <table class='theta-chain-table'>
        <thead>
          <tr class='theta-chain-group-row'>
            <th colspan='{len(_METRIC_COLUMNS)}' class='theta-chain-group-calls'>CALLS</th>
            <th class='theta-chain-group-strike'>STRIKE</th>
            <th colspan='{len(_METRIC_COLUMNS)}' class='theta-chain-group-puts'>PUTS</th>
          </tr>
          <tr>{call_header}<th class='theta-chain-strike'>&nbsp;</th>{put_header}</tr>
        </thead>
        <tbody>
          {''.join(body_rows)}
        </tbody>
      </table>
    </div>
    """
    st.markdown(table_html, unsafe_allow_html=True)
    legend_bits = ["<span class='theta-chain-legend-atm'>&#9679;</span> ATM strike"]
    if selected:
        legend_bits.append("<span class='theta-chain-legend-ai'>&#9679;</span> AI-selected strike")
    st.markdown(
        f"<div class='theta-chain-legend'>{' &nbsp;&nbsp; '.join(legend_bits)}</div>",
        unsafe_allow_html=True,
    )
