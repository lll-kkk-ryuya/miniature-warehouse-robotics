"""py3.10 compat-shim + floor-guard units (#563 / ADR-0008 追記 2026-08-30).

Independent-oracle tests (R-26 style, docs/architecture/20-dev-quality-and-testing.md §9):
every expected value below is a hand-written literal / stdlib identity, never computed
from the shim implementation.

The shim class ``_StrEnumShim`` is tested DIRECTLY on every interpreter: on the dev/CI
interpreter (py3.12) ``warehouse_interfaces.compat.StrEnum`` aliases the stdlib class and
the py3.10 branch never executes, so testing only the public alias would leave the shim
untested until it hits the Jetson board (Ubuntu 22.04 / py3.10). Mutation check: dropping
``__str__ = str.__str__`` from the shim reddens the ``shim`` half of the parametrized
semantics test on ANY interpreter (not only 3.10).

The AST floor guard below is the standing replacement for "grep and hope": it enforces
the declared ``requires-python = ">=3.10"`` floor (pyproject.toml:8) against the known
3.11+/3.12+ stdlib names, in every python file the repo can execute.

Guard limits (deliberate — the guard AIDS, it does not replace, real py3.10 runs):
import-NAME surface only. 3.11+/3.12+ *syntax* (``except*``, PEP 695) parses fine under
the 3.12 interpreter's ``ast`` and passes; method/behaviour deltas (``Task.cancelling()``,
3.11's widened ``datetime.fromisoformat``) are invisible to any import scan. The real
oracles are the py3.10 CI matrix (ADR-0008 残①, governance PR) and on-board pytest.
"""

import ast
import json
import sys
from pathlib import Path

import pytest
from warehouse_interfaces.compat import UTC, StrEnum, _StrEnumShim

REPO_ROOT = Path(__file__).resolve().parents[2]
_SELF = Path(__file__).resolve()

# Directory names never executed by the repo's own gates (vendored/derived/tooling trees).
_EXCLUDED_DIR_NAMES = {
    ".git",
    ".venv",
    "node_modules",
    "__pycache__",
    ".claude",
    ".codex",
    ".agents",
    ".pytest_cache",
}

# (module, name) -> replacement. Names that do not exist on Python 3.10 (the Jetson
# board floor, ADR-0008). Extend when a new floor break is discovered.
_BANNED_STDLIB_IMPORTS = {
    ("enum", "StrEnum"): "warehouse_interfaces.compat.StrEnum",
    ("enum", "ReprEnum"): "py3.11+ only — restructure without it",
    ("enum", "verify"): "py3.11+ only — restructure without it",
    ("enum", "member"): "py3.11+ only — restructure without it",
    ("enum", "nonmember"): "py3.11+ only — restructure without it",
    ("enum", "global_enum"): "py3.11+ only — restructure without it",
    ("enum", "EnumCheck"): "py3.11+ only — restructure without it",
    ("enum", "FlagBoundary"): "py3.11+ only — restructure without it",
    ("datetime", "UTC"): "warehouse_interfaces.compat.UTC (or datetime.timezone.utc)",
    ("typing", "Self"): "typing_extensions.Self",
    ("typing", "assert_never"): "typing_extensions.assert_never",
    ("typing", "assert_type"): "typing_extensions.assert_type",
    ("typing", "LiteralString"): "typing_extensions.LiteralString",
    ("typing", "Never"): "typing_extensions.Never (or typing.NoReturn)",
    ("typing", "Required"): "typing_extensions.Required",
    ("typing", "NotRequired"): "typing_extensions.NotRequired",
    ("typing", "Unpack"): "typing_extensions.Unpack",
    ("typing", "TypeVarTuple"): "typing_extensions.TypeVarTuple",
    ("typing", "dataclass_transform"): "typing_extensions.dataclass_transform",
    ("typing", "override"): "typing_extensions.override (3.12+)",
    ("typing", "TypeAliasType"): "typing_extensions.TypeAliasType (3.12+)",
    ("asyncio", "timeout"): "asyncio.wait_for",
    ("asyncio", "timeout_at"): "asyncio.wait_for",
    ("asyncio", "TaskGroup"): "explicit tasks + asyncio.gather",
    ("asyncio", "Runner"): "asyncio.run",
    ("asyncio", "Barrier"): "py3.11+ only — restructure without it",
    ("asyncio", "eager_task_factory"): "py3.12+ only — restructure without it",
    ("contextlib", "chdir"): "os.chdir + try/finally",
    ("itertools", "batched"): "manual slicing (3.12+)",
    ("hashlib", "file_digest"): "read + hashlib.new (3.11+)",
    ("math", "exp2"): "2**x (3.11+)",
    ("math", "cbrt"): "x ** (1/3) (3.11+)",
    ("inspect", "getmembers_static"): "py3.11+ only — restructure without it",
    ("sys", "exception"): "sys.exc_info()[1] (3.11+)",
}
_BANNED_STDLIB_MODULES = {
    "tomllib": 'pytest.importorskip("tomllib") inside the test body (3.11+ stdlib)',
}
_BANNED_MODULE_NAMES = {module for module, _ in _BANNED_STDLIB_IMPORTS}


# Allowlisted roots the repo's own gates execute on the py3.10 floor. An allowlist (not a
# repo-wide walk) so a stray venv/, vendored tree or incubator can never false-offend:
# plugins/ in particular declares its OWN floor (plugins/l3_zone_policy/pyproject.toml
# pins requires-python >= 3.12 and never runs on the board).
_SCAN_ROOTS = ("ws/src", "tests", "scripts", "spike", "deploy")
_SCAN_EXTRA_FILES = ("conftest.py",)


def _scan_py_files():
    """Every python file the repo's own gates can execute on the py3.10 floor.

    ws/src and tests/ run on the board; scripts/, spike/, deploy/ and the root conftest
    are host / board harnesses under the same declared floor. Excludes vendored/derived
    dirs, compat.py (the version-guarded shim itself) and this file (it deliberately
    contains banned patterns as probes).
    """
    candidates = [REPO_ROOT / name for name in _SCAN_EXTRA_FILES]
    for root in _SCAN_ROOTS:
        candidates.extend(sorted((REPO_ROOT / root).rglob("*.py")))
    for path in candidates:
        if not path.is_file():
            continue
        parts = path.relative_to(REPO_ROOT).parts
        if any(part in _EXCLUDED_DIR_NAMES for part in parts):
            continue
        if path.resolve() == _SELF:
            continue
        if path.name == "compat.py" and path.parent.name == "warehouse_interfaces":
            continue
        yield path


def _parsed(path):
    return ast.parse(path.read_text(encoding="utf-8"), filename=str(path))


def _class_base_names(node):
    return {b.id for b in node.bases if isinstance(b, ast.Name)} | {
        b.attr for b in node.bases if isinstance(b, ast.Attribute)
    }


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
    which is exactly the silent breakage these literals pin down. (On py3.10 the alias IS
    the shim, so the two parametrized cases collapse into one — by design.)
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


def test_utc_is_the_stdlib_timezone_utc_singleton():
    """compat.UTC must be the SAME object that 3.11+ aliases as ``datetime.UTC`` (#563).

    Identity (not mere equality) is the oracle: a lookalike tzinfo instance would be
    equal-but-distinct, so ``is`` comparisons and pickling identity could silently split
    between the board (py3.10) and dev (py3.12).
    """
    from datetime import timezone

    assert UTC is timezone.utc
    if sys.version_info >= (3, 11):
        import datetime as _dt

        assert UTC is _dt.UTC


def test_auto_diverges_on_shim_so_it_is_banned():
    """Document WHY auto() is prohibited: the REAL shim silently yields "1", never "unknown".

    Real 3.11+ StrEnum lower-cases the member name; the shim inherits plain Enum numbering
    and raises no error — a silent wire-format corruption. Hence the AST ban below.
    """
    from enum import auto

    class Bad(_StrEnumShim):
        UNKNOWN = auto()

    assert str(Bad.UNKNOWN) == "1"
    assert str(Bad.UNKNOWN) != "unknown"


def test_no_py311_only_stdlib_imports_anywhere():
    """AST floor guard for ``requires-python = ">=3.10"`` (#563 / ADR-0008 残①の繋ぎ).

    Catches aliased (``import X as Y``), parenthesized multi-line, star and
    attribute-access (``enum.StrEnum`` / ``datetime.UTC``) forms that a line regex
    misses. A stray banned import either ImportErrors on the py3.10 board outright,
    or — for a re-added local shim — silently breaks the shared-class ``isinstance``
    JSON paths (conversation_events._jsonify).
    """
    offenders = []
    for path in _scan_py_files():
        rel = path.relative_to(REPO_ROOT)
        try:
            tree = _parsed(path)
        except SyntaxError as exc:
            offenders.append(f"{rel}: unparseable ({exc})")
            continue
        # First pass: module aliases, so `import datetime as dt` + `dt.UTC` is caught too.
        module_aliases = {}
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    root = alias.name.split(".")[0]
                    if root in _BANNED_MODULE_NAMES or root in _BANNED_STDLIB_MODULES:
                        module_aliases[alias.asname or alias.name] = root
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.level == 0:
                if node.module in _BANNED_STDLIB_MODULES:
                    offenders.append(
                        f"{rel}:{node.lineno} from {node.module} import ... — use "
                        f"{_BANNED_STDLIB_MODULES[node.module]}"
                    )
                    continue
                for alias in node.names:
                    if alias.name == "*" and node.module in _BANNED_MODULE_NAMES:
                        offenders.append(f"{rel}:{node.lineno} from {node.module} import *")
                    elif (node.module, alias.name) in _BANNED_STDLIB_IMPORTS:
                        offenders.append(
                            f"{rel}:{node.lineno} from {node.module} import {alias.name} — "
                            f"use {_BANNED_STDLIB_IMPORTS[(node.module, alias.name)]}"
                        )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name in _BANNED_STDLIB_MODULES:
                        offenders.append(f"{rel}:{node.lineno} import {alias.name}")
            elif isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
                mod = module_aliases.get(node.value.id, node.value.id)
                if (mod, node.attr) in _BANNED_STDLIB_IMPORTS:
                    offenders.append(f"{rel}:{node.lineno} {node.value.id}.{node.attr}")
    assert offenders == [], "\n".join(offenders)


def test_no_local_str_enum_mixin_redefinition():
    """Single-shared-class invariant (#563): only compat.py may define the str+Enum mixin.

    A module-local ``class X(str, Enum)`` shadow shim keeps working per-module but silently
    breaks ``isinstance(value, StrEnum)`` JSON serialization (conversation_events._jsonify)
    — the exact risk that mandated ONE shared class in warehouse_interfaces.compat.
    """
    offenders = []
    for path in _scan_py_files():
        rel = path.relative_to(REPO_ROOT)
        tree = _parsed(path)
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and {"str", "Enum"} <= _class_base_names(node):
                offenders.append(f"{rel}:{node.lineno} class {node.name}(str, Enum)")
    assert offenders == [], "\n".join(offenders)


def test_no_auto_in_strenum_class_bodies():
    """auto() ban inside StrEnum bodies (see divergence test above).

    AST-scoped to class bodies whose bases mention StrEnum (or the raw str+Enum mixin),
    so docstrings / comments / unrelated auto() can never false-positive.
    """
    offenders = []
    for path in _scan_py_files():
        rel = path.relative_to(REPO_ROOT)
        tree = _parsed(path)
        for node in ast.walk(tree):
            if not isinstance(node, ast.ClassDef):
                continue
            bases = _class_base_names(node)
            if "StrEnum" not in bases and not {"str", "Enum"} <= bases:
                continue
            for sub in ast.walk(node):
                is_auto_call = isinstance(sub, ast.Call) and (
                    (isinstance(sub.func, ast.Name) and sub.func.id == "auto")
                    or (isinstance(sub.func, ast.Attribute) and sub.func.attr == "auto")
                )
                if is_auto_call:
                    offenders.append(f"{rel}:{node.lineno} class {node.name}: auto()")
    assert offenders == [], "\n".join(offenders)


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
    The REAL production paths are additionally pinned in test_l3_pipeline.py
    (test_compile_rejected_plan_zero_dispatch_end_to_end) and test_x_er_cycle.py
    (test_plugin_reject_zero_dispatch_store_and_gen_untouched).
    """
    from warehouse_llm_bridge.robotics_planning_core.validator.report import ValidationStatus

    assert f"validation status={ValidationStatus.REJECTED}" == "validation status=rejected"
    assert f"{ValidationStatus.EMERGENCY_STOP}" == "emergency_stop"
    assert f"{ValidationStatus.NEEDS_CLARIFICATION}" == "needs_clarification"
