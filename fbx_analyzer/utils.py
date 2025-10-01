"""Shared helper utilities for interacting with the FBX SDK."""

from __future__ import annotations

from typing import Any, Iterable, Tuple, TypeVar

Vector3 = Tuple[float, float, float]
T = TypeVar("T")


def resolve_enum_value(enum_holder: Any, target_name: str) -> Any:
    """Return an enum value by name, handling SDK layout differences.

    Autodesk regularly shuffles where enumeration members live between
    releases.  Sometimes they are attributes on the class, other times they
    are nested under helper containers such as ``EType``.  This helper performs
    a best-effort lookup so callers can request an enum using a friendly name
    without needing to know the exact SDK flavour they are running against.
    """

    if hasattr(enum_holder, target_name):
        return getattr(enum_holder, target_name)

    for attr_name in dir(enum_holder):
        if attr_name.lower() == target_name.lower():
            return getattr(enum_holder, attr_name)

    nested = getattr(enum_holder, "EType", None)
    if nested is not None:
        for attr_name in dir(nested):
            if attr_name.lower() == target_name.lower():
                return getattr(nested, attr_name)

    raise AttributeError(
        f"Unable to resolve enum value '{target_name}' from {enum_holder!r}"
    )


def double3_to_tuple(vector: Iterable[T]) -> Vector3:
    """Convert FBX ``FbxDouble3`` style objects into a plain tuple."""

    values = list(vector)
    if len(values) != 3:
        raise ValueError("Expected a 3 component vector.")
    return (float(values[0]), float(values[1]), float(values[2]))

