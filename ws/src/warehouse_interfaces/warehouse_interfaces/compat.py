"""Python-version compatibility shims for the frozen-contract packages (#563).

``enum.StrEnum`` exists only on Python >= 3.11; the Jetson prod board runs
Ubuntu 22.04 / Python 3.10 (ADR-0008). On 3.11+ this module re-exports the
stdlib class unchanged, so every existing environment keeps byte-identical
behaviour; on 3.10 it provides ``_StrEnumShim``, verified observationally
identical to the stdlib class across str()/f-string/format()/json/pydantic-v2
on CPython 3.10-3.13 (#563).

This must stay the SINGLE shared ``StrEnum`` for the whole workspace: a stray
per-module shim would break ``isinstance(value, StrEnum)`` JSON serialization
paths (e.g. conversation_events._jsonify) silently. Enforced by the
source-scan test in ``tests/unit/test_py310_compat.py``.

Do NOT use ``enum.auto()`` with members of this class: on the 3.10 shim it
silently yields ``"1"`` instead of the lower-cased member name and raises no
error. All members must use explicit string values (also enforced by
``tests/unit/test_py310_compat.py``).
"""

import sys
from enum import Enum


class _StrEnumShim(str, Enum):
    """str-valued Enum matching the observable behaviour of 3.11+ ``enum.StrEnum``.

    ``__str__ = str.__str__`` is REQUIRED: without it ``str(member)`` / f-strings
    yield ``"ClassName.MEMBER"`` and break round-trip parses such as
    ``ConversationEvent.from_dict``'s default-verdict path (#563). ``__format__``
    is intentionally omitted — 3.10 ``Enum.__format__`` detects the ``__str__``
    override and routes to ``str(self)`` (verified on CPython 3.10.1-3.10.19).
    """

    __str__ = str.__str__


if sys.version_info >= (3, 11):
    from enum import StrEnum
else:  # Python 3.10 (Jetson / Humble, ADR-0008)
    StrEnum = _StrEnumShim


__all__ = ["StrEnum"]
