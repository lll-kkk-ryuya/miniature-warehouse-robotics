"""py3.10 StrEnum-shim + sweep-enforcement units (#563 / ADR-0008 追記 2026-08-30).

Independent-oracle tests (R-26 style, docs/architecture/20-dev-quality-and-testing.md §9):
every expected string below is a hand-written literal, never computed from the shim.

The shim class ``_StrEnumShim`` is tested DIRECTLY on every interpreter: on the dev/CI
interpreter (py3.12) ``warehouse_interfaces.compat.StrEnum`` aliases the stdlib class and
the py3.10 branch never executes, so testing only the public alias would leave the shim
untested until it hits the Jetson board (Ubuntu 22.04 / py3.10). Mutation check: dropping
``__str__ = str.__str__`` from the shim reddens the ``shim`` half of the parametrized
semantics test on ANY interpreter (not only 3.10).
"""

import json
import re
import sys
from pathlib import Path

import pytest
from warehouse_interfaces.compat import StrEnum, _StrEnumShim

WS_SRC = Path(__file__).resolve().parents[2] / "ws" / "src"


class _ShimColor(_StrEnumShim):
    RED = "red"
    DARK_BLUE = "dark-blue"


class _AliasColor(StrEnum):
    RED = "red"
    DARK_BLUE = "dark-blue"


@pytest.mark.parametrize("enum_cls", [_ShimColor, _AliasColor], ids=["shim", "public-alias"])
def test_str_semantics_match_py311_strenum(enum_cls):
    """The observable contract the codebase depends on (str()/f-string/format/json/eq).

    A naive ``class X(str, Enum)`` WITHOUT ``__str__`` yields ``"_ShimColor.RED"`` from
    ``str()``/f-strings on 3.11+ (and diverges on ``format()`` between 3.10 and 3.11+),
    which is exactly the silent breakage these literals pin down.
    """
    member = enum_cls.RED
    assert str(member) == "red"
    assert f"{member}" == "red"
    assert format(member) == "red"
    assert "%s" % member == "red"  # noqa: UP031 — deliberate %-formatting semantics probe
    assert json.dumps(member) == '"red"'
    assert member == "red"
    assert enum_cls("red") is member
    # str() round-trip — the ConversationEvent.from_dict default-verdict re-parse shape.
    assert enum_cls(str(member)) is member
    assert str(enum_cls.DARK_BLUE) == "dark-blue"


@pytest.mark.skipif(sys.version_info < (3, 11), reason="stdlib StrEnum absent on py3.10")
def test_shim_matches_stdlib_strenum_observably():
    """Differential oracle: shim vs the real 3.11+ ``enum.StrEnum``, same members."""
    from enum import StrEnum as StdlibStrEnum

    class Stdlib(StdlibStrEnum):
        RED = "red"

    class Shim(_StrEnumShim):
        RED = "red"

    assert str(Shim.RED) == str(Stdlib.RED)
    assert f"{Shim.RED}" == f"{Stdlib.RED}"
    assert format(Shim.RED) == format(Stdlib.RED)
    assert json.dumps(Shim.RED) == json.dumps(Stdlib.RED)
    assert Shim.RED.value == Stdlib.RED.value
    assert (Shim.RED == "red") is (Stdlib.RED == "red")


def test_auto_diverges_on_shim_so_it_is_banned():
    """Document WHY auto() is prohibited: the shim silently yields "1", never "unknown".

    Real 3.11+ StrEnum lower-cases the member name; the shim inherits plain Enum numbering
    and raises no error — a silent wire-format corruption. Hence the source-scan ban below.
    """
    from enum import Enum, auto

    class Bad(str, Enum):  # the shim shape, deliberately reconstructed with auto()
        __str__ = str.__str__
        UNKNOWN = auto()

    assert str(Bad.UNKNOWN) == "1"
    assert str(Bad.UNKNOWN) != "unknown"


def test_no_direct_stdlib_strenum_import_outside_compat():
    """Single-shared-class invariant (#563): StrEnum must come from warehouse_interfaces.compat.

    A stray ``from enum import StrEnum`` either ImportErrors on the py3.10 board, or — if
    someone re-adds a local shim — silently breaks ``isinstance(value, StrEnum)`` JSON
    paths (conversation_events._jsonify). This scan makes the sweep permanent.
    """
    offenders = []
    for path in sorted(WS_SRC.rglob("*.py")):
        if path.name == "compat.py" and path.parent.name == "warehouse_interfaces":
            continue  # the version-guarded re-export itself
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
            if re.search(r"^\s*from\s+enum\s+import\s+.*\bStrEnum\b", line):
                offenders.append(f"{path.relative_to(WS_SRC)}:{lineno}")
    assert offenders == []


def test_no_auto_in_modules_using_compat_strenum():
    """auto() ban (see divergence test above) for every module importing the compat StrEnum."""
    offenders = []
    for path in sorted(WS_SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "from warehouse_interfaces.compat import StrEnum" not in text:
            continue
        if re.search(r"\bauto\s*\(", text):
            offenders.append(str(path.relative_to(WS_SRC)))
    assert offenders == []


def test_repo_enums_subclass_the_single_compat_strenum():
    """The three previously-unprotected str()-semantics sites share ONE StrEnum class."""
    from warehouse_interfaces.schemas import CommandAction
    from warehouse_llm_bridge.conversation_events import ConversationVerdict
    from warehouse_llm_bridge.robotics_planning_core.validator.report import ValidationStatus

    for enum_cls in (CommandAction, ConversationVerdict, ValidationStatus):
        assert issubclass(enum_cls, StrEnum), enum_cls


def test_conversation_event_default_verdict_survives_str_roundtrip():
    """Pins conversation_events.from_dict's ``str(enum-default)`` re-parse (#563).

    With a __str__-less enum this raises ``ValueError: 'ConversationVerdict.PENDING' is
    not a valid ConversationVerdict`` — previously no test covered this path.
    """
    from warehouse_llm_bridge.conversation_events import ConversationEvent, ConversationVerdict

    assert str(ConversationVerdict.PENDING) == "pending"
    event = ConversationEvent.from_dict(
        {
            "event_id": "e1",
            "episode_id": "ep1",
            "actor": "bot1",
            "audience": "bot2",
            "intent": "inform",
        }
    )
    assert event.verdict is ConversationVerdict.PENDING


def test_validation_status_fstring_yields_raw_value():
    """Pins the x_er_cycle / pipeline 0-dispatch reasoning strings' enum rendering (#563).

    Both build ``f"... (validation status={report.status})"`` — the operator-facing
    ``Command.reasoning`` must carry ``rejected``, not ``ValidationStatus.REJECTED``.
    """
    from warehouse_llm_bridge.robotics_planning_core.validator.report import ValidationStatus

    assert f"validation status={ValidationStatus.REJECTED}" == "validation status=rejected"
    assert f"{ValidationStatus.EMERGENCY_STOP}" == "emergency_stop"
    assert f"{ValidationStatus.NEEDS_CLARIFICATION}" == "needs_clarification"
