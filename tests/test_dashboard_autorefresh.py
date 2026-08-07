"""Unit tests for dashboard.utils.autorefresh."""

from __future__ import annotations

from dashboard.utils.autorefresh import live_fragment


class TestLiveFragmentBareMode:
    """No active Streamlit script run context (unit tests / bare mode)."""

    def test_renders_exactly_once_synchronously(self) -> None:
        calls: list[int] = []

        live_fragment(lambda: calls.append(1), interval_seconds=1.0, key="test_key")

        assert calls == [1]

    def test_never_raises_when_st_fragment_is_absent(self) -> None:
        calls: list[int] = []

        live_fragment(lambda: calls.append(1), interval_seconds=0.5, key="another_key")

        assert len(calls) == 1
