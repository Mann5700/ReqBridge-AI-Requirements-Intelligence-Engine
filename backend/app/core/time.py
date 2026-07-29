"""Time helpers — single place to evolve when we move to tz-aware columns.

``datetime.utcnow()`` is deprecated in Python 3.12+ (warning) and removed in
later versions. Until we migrate the DB schema to ``TIMESTAMP WITH TIME ZONE``
we need a naive UTC value to keep parity with existing rows.
"""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    """Naive UTC ``datetime`` — drop-in replacement for ``datetime.utcnow()``.

    Computed via ``datetime.now(timezone.utc)`` (the recommended modern API)
    and then stripped of tzinfo so SQLAlchemy's ``DateTime`` (timezone=False)
    columns store the value without surprises.
    """
    return datetime.now(timezone.utc).replace(tzinfo=None)
