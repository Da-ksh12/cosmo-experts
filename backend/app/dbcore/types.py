"""Shared database value types."""
from __future__ import annotations

import time


class Epoch(float):
    """A point in time as Unix seconds.

    A float subclass, so all existing arithmetic, comparisons, JSON output and SQLite binding keep working unchanged. The PostgreSQL backend stores it as timestamptz
    (timezone-aware) and returns timestamptz columns as Epoch again, so application code sees the same values on both databases. Adding or subtracting a plain number
    yields an Epoch; subtracting two Epochs yields a duration in seconds."""
    __slots__ = ()

    def __add__(self, other):
        return Epoch(float(self) + float(other))

    __radd__ = __add__

    def __sub__(self, other):
        if isinstance(other, Epoch):
            return float(self) - float(other)
        return Epoch(float(self) - float(other))

    def __repr__(self) -> str:
        return f"Epoch({float(self)!r})"


def now() -> Epoch:
    return Epoch(time.time())
