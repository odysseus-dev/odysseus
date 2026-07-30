"""Regression for issue #5697 — skill timestamps must not use ``datetime.utcnow()``.

``_now_iso()`` builds the ``created`` value in skill frontmatter. ``utcnow()``
returns a *naive* datetime and has been deprecated since Python 3.12, scheduled
for removal. The replacement must stay timezone-aware while keeping the
serialized ``YYYY-MM-DDTHH:MM:SSZ`` shape, so skill files written by older
versions keep parsing.

The UTC check matters on its own: a bare ``datetime.now()`` also produces the
right shape, but emits local wall time, which would silently backdate or
postdate skills for every user outside UTC.
"""

import os
import re
import time
import warnings
from datetime import datetime, timezone

import pytest

from services.memory.skill_format import _now_iso

_ISO_Z = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def test_now_iso_keeps_serialized_shape():
    assert _ISO_Z.match(_now_iso())


def test_now_iso_emits_no_deprecation_warning():
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        _now_iso()
    assert not [w for w in caught if issubclass(w.category, DeprecationWarning)]


@pytest.mark.skipif(
    not hasattr(time, "tzset"),
    reason="time.tzset is unavailable on this platform",
)
def test_now_iso_is_utc_not_local_time():
    """Pin UTC under a non-UTC local timezone, where the two visibly diverge."""
    original_tz = os.environ.get("TZ")
    os.environ["TZ"] = "Asia/Amman"  # UTC+3, never UTC
    time.tzset()
    try:
        emitted = datetime.strptime(_now_iso(), "%Y-%m-%dT%H:%M:%SZ").replace(
            tzinfo=timezone.utc
        )
        drift = abs((emitted - datetime.now(timezone.utc)).total_seconds())
        assert drift < 60, f"timestamp is {drift}s off UTC — local time leaked in"
    finally:
        if original_tz is None:
            os.environ.pop("TZ", None)
        else:
            os.environ["TZ"] = original_tz
        time.tzset()
