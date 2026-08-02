"""Immutable copying helpers for Kite SDK responses."""

from __future__ import annotations

from types import MappingProxyType
from typing import Mapping, Sequence


def copy_to_immutable(value: object) -> object:
    """Recursively copy a value into immutable structures.

    Args:
        value: Kite SDK response fragment.

    Returns:
        Immutable copy using tuples and MappingProxyType.
    """
    if isinstance(value, Mapping):
        return MappingProxyType(
            {key: copy_to_immutable(item) for key, item in value.items()}
        )
    if isinstance(value, list):
        return tuple(copy_to_immutable(item) for item in value)
    if isinstance(value, tuple):
        return tuple(copy_to_immutable(item) for item in value)
    return value


def copy_mapping(value: Mapping[str, object]) -> Mapping[str, object]:
    """Return an immutable mapping copy."""
    copied = copy_to_immutable(dict(value))
    assert isinstance(copied, Mapping)
    return copied


def copy_mapping_batch(
    value: Mapping[str, Mapping[str, object]],
) -> Mapping[str, Mapping[str, object]]:
    """Return an immutable nested mapping copy."""
    return MappingProxyType(
        {key: copy_mapping(mapping) for key, mapping in value.items()}
    )


def copy_sequence(value: Sequence[Mapping[str, object]]) -> tuple[Mapping[str, object], ...]:
    """Return an immutable tuple of mapping copies."""
    return tuple(copy_mapping(item) for item in value)
