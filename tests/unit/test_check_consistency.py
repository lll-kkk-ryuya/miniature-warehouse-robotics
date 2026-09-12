"""Unit tests for ``scripts/check_consistency.py``.

Part 1 — the B4 cross-file ``doc:line`` reference drift check added for #177 (a
regression guard for the #165 class of bug, where inserting lines into a doc silently
broke every ``docNN:LINE`` reference pointing past the insertion).

Part 2 — the A3/A4 frozen-safety-number checks added for #652 (docs re-typing
``MAX_LINEAR_VELOCITY`` / ``IDLE_SPEED_EPS`` must match ``warehouse_interfaces.safety``).

Pure-logic tests (no ROS / hardware) → ``unit`` marker, NOT ``safety``: this check is
governance tooling, not an Emergency Guardian / Policy Gate / speed-clamp invariant.

The checker is loaded by file path (it is a script, not a package module) and its
module-level ``ROOT`` / ``DOCS`` globals are monkeypatched onto a synthetic temp tree,
so the tests are hermetic and do not depend on the live repo corpus — with two stated
exceptions that must read the real sources: ``test_sources_expose_frozen_safety_numbers``
(AST loader vs a real import) and ``test_checks_are_not_no_ops_on_the_live_corpus``.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.unit


def _load_checker():
    spec = importlib.util.spec_from_file_location(
        "check_consistency_under_test", _REPO / "scripts" / "check_consistency.py"
    )
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    # Register before exec_module: @dataclass resolves cls.__module__ via sys.modules.
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


cc = _load_checker()


# ── _anchor_lost: the structural "no citable anchor" predicate ─────────────────


@pytest.mark.parametrize(
    "line, lost",
    [
        ("", True),  # blank
        ("    ", True),  # whitespace-only
        ("|---|---|", True),  # table separator
        ("| --- | :---: | ---: |", True),  # table separator with alignment
        ("|--------------|-------------------|", True),  # wide table separator
        ("---", True),  # horizontal rule
        ("***", True),  # horizontal rule
        ("___", True),  # horizontal rule
        ("----", True),  # 4-dash horizontal rule
        ("### 全体像", False),  # heading → valid anchor
        ("> prose line", False),  # prose → valid anchor
        ("- list item", False),  # leading dash but NOT a rule
        ("* bullet", False),  # leading star but NOT a rule
        ("| a | b |", False),  # table CONTENT row (no dashes) → valid anchor
        ("| foo-bar | baz |", False),  # content row with a dash → still valid
        ("real content", False),
    ],
)
def test_anchor_lost(line, lost):
    assert (cc._anchor_lost(line) is not None) is lost


# ── _doc_number_index: ambiguous numbers are dropped (never flagged) ───────────


def test_doc_number_index_drops_ambiguous(tmp_path, monkeypatch):
    arch = tmp_path / "docs" / "architecture"
    dev = tmp_path / "docs" / "dev"
    arch.mkdir(parents=True)
    dev.mkdir(parents=True)
    (arch / "12-infra.md").write_text("x\n", encoding="utf-8")
    # two files share the 03- prefix → ambiguous, must be dropped
    (arch / "03-software.md").write_text("x\n", encoding="utf-8")
    (dev / "03-retro.md").write_text("x\n", encoding="utf-8")
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")
    idx = cc._doc_number_index()
    assert idx["12"].name == "12-infra.md"  # unique → resolves
    assert "03" not in idx  # ambiguous → dropped


# ── full check: flags blank / separator / rule / EOF, spares healthy anchors ───


def _build_doc(tmp_path) -> Path:
    """A 5-line doc: L1 heading, L2 blank, L3 table-sep, L4 hrule, L5 content."""
    arch = tmp_path / "docs" / "architecture"
    arch.mkdir(parents=True)
    doc = arch / "12-infra.md"
    doc.write_text(
        "# Heading L1\n"  # 1 valid anchor
        "\n"  # 2 blank → anchor lost
        "|---|---|\n"  # 3 table separator → anchor lost
        "---\n"  # 4 horizontal rule → anchor lost
        "real content line\n",  # 5 valid anchor
        encoding="utf-8",
    )
    return doc


def test_check_flags_drift_and_spares_healthy(tmp_path, monkeypatch):
    _build_doc(tmp_path)
    pkg = tmp_path / "ws" / "pkg"
    pkg.mkdir(parents=True)
    (pkg / "CLAUDE.md").write_text(
        "ok: doc12:1\n"  # line 1 → heading → clean
        "blank: doc12:2\n"  # line 2 → blank → WARN
        "sep: doc12:3\n"  # line 3 → table sep → WARN
        "rule: doc12:4\n"  # line 4 → hrule → WARN
        "content: doc12:5\n"  # line 5 → content → clean
        "eof: doc12:99\n",  # line 6 → past EOF → WARN
        encoding="utf-8",
    )
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")

    findings = cc.check_cross_doc_line_refs(None, None)
    by_line = {f.line: f for f in findings}

    assert set(by_line) == {2, 3, 4, 6}  # only the broken refs, at their own lines
    assert all(f.level == cc.WARN and f.rule == "B4-doc-line-ref" for f in findings)
    assert by_line[2].file.endswith("CLAUDE.md")
    assert "blank line" in by_line[2].message
    assert "table-separator" in by_line[3].message
    assert "horizontal-rule" in by_line[4].message
    assert "past EOF" in by_line[6].message


def test_check_skips_ambiguous_doc_number(tmp_path, monkeypatch):
    arch = tmp_path / "docs" / "architecture"
    dev = tmp_path / "docs" / "dev"
    arch.mkdir(parents=True)
    dev.mkdir(parents=True)
    (arch / "03-software.md").write_text("# H\n\n", encoding="utf-8")  # L2 blank
    (dev / "03-retro.md").write_text("# H\n\n", encoding="utf-8")
    ws = tmp_path / "ws"
    ws.mkdir()
    # doc03:2 WOULD be a blank-line hit, but doc03 is ambiguous → must be skipped
    (ws / "note.md").write_text("ref: doc03:2\n", encoding="utf-8")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")

    assert cc.check_cross_doc_line_refs(None, None) == []


def test_check_path_form_resolution(tmp_path, monkeypatch):
    _build_doc(tmp_path)
    rules = tmp_path / ".claude" / "rules"
    rules.mkdir(parents=True)
    # repo-root-relative path form → resolves via ROOT, line 2 is blank → WARN
    (rules / "r.md").write_text("see docs/architecture/12-infra.md:2 here\n", encoding="utf-8")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")

    findings = cc.check_cross_doc_line_refs(None, None)
    assert len(findings) == 1
    assert findings[0].rule == "B4-doc-line-ref"
    assert findings[0].file.endswith("r.md")
    assert "blank line" in findings[0].message


def test_check_range_end_past_eof(tmp_path, monkeypatch):
    _build_doc(tmp_path)  # 5-line doc
    ws = tmp_path / "ws"
    ws.mkdir()
    # start (1) is a valid heading, but the range END (99) is past EOF → WARN
    (ws / "n.md").write_text("range: doc12:1-99\n", encoding="utf-8")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")

    findings = cc.check_cross_doc_line_refs(None, None)
    assert len(findings) == 1
    assert "past EOF" in findings[0].message


def test_check_skips_agent_worktrees(tmp_path, monkeypatch):
    """Files under .claude/worktrees/ (subagent isolation repo copies) are never
    scanned — they would duplicate every B4 ref as spurious WARNs."""
    _build_doc(tmp_path)
    wt = tmp_path / ".claude" / "worktrees" / "x" / ".claude" / "rules"
    wt.mkdir(parents=True)
    # doc12:2 is a blank-line hit → WOULD warn if the worktree copy were scanned
    (wt / "r.md").write_text("ref: doc12:2\n", encoding="utf-8")
    # sibling dir named "worktrees" NOT under .claude/ must still be scanned
    ws_wt = tmp_path / "ws" / "worktrees"
    ws_wt.mkdir(parents=True)
    (ws_wt / "n.md").write_text("ref: doc12:2\n", encoding="utf-8")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")

    findings = cc.check_cross_doc_line_refs(None, None)
    assert len(findings) == 1  # only the ws/worktrees/ ref, not the agent copy
    assert findings[0].file.endswith("n.md")
    assert ".claude/worktrees" not in findings[0].file


def test_check_skips_per_file_mode(tmp_path, monkeypatch):
    _build_doc(tmp_path)
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "n.md").write_text("ref: doc12:2\n", encoding="utf-8")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")

    # `only` set (per-file / pre-commit mode) → cross-file scan is a no-op
    only = [tmp_path / "docs" / "architecture" / "12-infra.md"]
    assert cc.check_cross_doc_line_refs(None, only) == []


# ── A3 / A4: docs-side copies of the frozen safety numbers (#652) ──────────────
#
# Independent oracle: every expectation below is written from the CONTRACT (the doc
# line asserts value V; the frozen value is F; V != F must be an ERROR that names
# both), never from the checker's regexes. The frozen numbers are hand-written here
# and cross-checked against warehouse_interfaces.safety in
# ``test_sources_expose_frozen_safety_numbers``.

_FROZEN_CAP = 0.3  # safety.py:18 MAX_LINEAR_VELOCITY
_FROZEN_EPS = 0.01  # safety.py (module end) IDLE_SPEED_EPS


def _src(cap=_FROZEN_CAP, eps=_FROZEN_EPS):
    """A ``Sources`` carrying the two numbers the A3/A4 checks read."""
    return cc.Sources(
        max_linear_velocity=cap,
        idle_speed_eps=eps,
        battery_critical_pct=10,
        battery_low_pct=20,
        robot_radius=0.075,
        known_locations=set(),
    )


def _write_doc(tmp_path, monkeypatch, text, name="12-infra.md"):
    d = tmp_path / "docs" / "architecture"
    d.mkdir(parents=True, exist_ok=True)
    doc = d / name
    doc.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8")
    monkeypatch.setattr(cc, "ROOT", tmp_path)
    monkeypatch.setattr(cc, "DOCS", tmp_path / "docs")
    return doc


def test_sources_expose_frozen_safety_numbers():
    """The AST loader must surface BOTH numbers — an independent import is the oracle."""
    from warehouse_interfaces.safety import IDLE_SPEED_EPS, MAX_LINEAR_VELOCITY

    src = cc.load_sources()
    assert src.max_linear_velocity == MAX_LINEAR_VELOCITY == _FROZEN_CAP
    assert src.idle_speed_eps == IDLE_SPEED_EPS == _FROZEN_EPS


def test_new_checks_are_registered():
    assert cc.check_speed_cap in cc.CHECKS
    assert cc.check_idle_speed_eps in cc.CHECKS


@pytest.mark.parametrize(
    "line",
    [
        # covered forms asserting the CORRECT value → silent
        "ハード速度上限 `MAX_LINEAR_VELOCITY = 0.3 m/s`（safety.py:18）",
        "`MAX_LINEAR_VELOCITY=0.3` m/s を MCU 内で強制する",
        "凍結値（`MAX_LINEAR_VELOCITY 0.3`）を floor とする",
        "`MAX_LINEAR_VELOCITY`（0.3）は hard cap",
        "MAX_LINEAR_VELOCITY: float = 0.3  # m/s",
        "`MAX_LINEAR_VELOCITY` = 0.300 m/s",  # trailing zeros compare numerically
        # OUT OF SCOPE by design (doc04 §5 known limits) → must stay silent
        "速度上限は 0.25 m/s とする",  # bare number, constant not named
        "通路帯は 0.15 m/s、狭所は 0.05 m/s",  # other speeds entirely
        "`MAX_LINEAR_VELOCITY` は safety.py:18 が正本",  # name, no number next to it
        "`min(帯値, MAX_LINEAR_VELOCITY)`）②`0.0`・非有限は停止",  # number of ANOTHER clause
        # the constant is only a SUFFIX of the env-override name → NOT a copy of the cap.
        # config LOWERS the cap by design (0 < cap <= hard cap), so a documented override
        # asserting a DIFFERENT value is a correct example, not drift (shape: config.py:25).
        "`WAREHOUSE__SAFETY__MAX_LINEAR_VELOCITY=0.25` で運用値を下げる",
        # negation-guarded prose (explaining an old value) → skipped like A1/A2
        "旧 `MAX_LINEAR_VELOCITY = 0.25` は誤り",
    ],
)
def test_speed_cap_clean_lines(tmp_path, monkeypatch, line):
    _write_doc(tmp_path, monkeypatch, line)
    assert cc.check_speed_cap(_src(), None) == []


@pytest.mark.parametrize(
    "line, doc_value",
    [
        ("`MAX_LINEAR_VELOCITY = 0.25 m/s`", "0.25"),
        ("MAX_LINEAR_VELOCITY=0.5", "0.5"),
        ("MAX_LINEAR_VELOCITY: float = 0.8  # platform max", "0.8"),
    ],
)
def test_speed_cap_mismatch_is_error(tmp_path, monkeypatch, line, doc_value):
    _write_doc(tmp_path, monkeypatch, line)
    findings = cc.check_speed_cap(_src(), None)

    assert len(findings) == 1
    f = findings[0]
    assert f.level == cc.ERROR  # a wrong cap must RED the gate, not just warn
    assert f.rule == "A3-speed-cap"
    assert f.file.endswith("12-infra.md") and f.line == 1
    assert doc_value in f.message  # the doc's value
    assert "0.3" in f.message  # the frozen value
    assert "docs を凍結契約に合わせる" in f.message  # direction of the fix


@pytest.mark.parametrize(
    "line",
    [
        # covered forms asserting the CORRECT value → silent
        "- **正本（単一ソース）**: `IDLE_SPEED_EPS: float = 0.01  # m/s`",
        "`idle 率 = (|v| ≤ ε=0.01 m/s のサンプル数) ÷ 総数`",
        "ε = 0.01 m/s",
        "凍結先 = `warehouse_interfaces.safety.IDLE_SPEED_EPS`（0.01 m/s・#649）",
        "`IDLE_SPEED_EPS` 0.010 m/s",  # textual form differs, value is equal
        "`IDLE_SPEED_EPS` 1e-2 m/s",  # exponent form, value is equal
        # OUT OF SCOPE by design (doc04 §5 known limits) → must stay silent
        "|v| ≤ 0.01 m/s なら idle とみなす",  # bare number, constant not named
        "robot_localization は 0 分散を ε=1e-6 に置換する",  # ε WITHOUT m/s = another ε
        "`IDLE_SPEED_EPS` は doc12 §4 の定義に従う",  # prose gap: never capture the `12`
        # negation-guarded prose → skipped like A1/A2
        "従来の `IDLE_SPEED_EPS = 0.05` は使わない",
    ],
)
def test_idle_speed_eps_clean_lines(tmp_path, monkeypatch, line):
    _write_doc(tmp_path, monkeypatch, line)
    assert cc.check_idle_speed_eps(_src(), None) == []


@pytest.mark.parametrize(
    "line, doc_value",
    [
        ("`IDLE_SPEED_EPS: float = 0.02`", "0.02"),
        ("ε=0.02 m/s 以下を idle とする", "0.02"),
        ("`IDLE_SPEED_EPS`（0.1 m/s）", "0.1"),
        # the same two "textual form" arms as the clean cases, but WRONG — so a
        # dropped exponent/trailing-zero arm cannot pass by simply matching nothing
        ("`IDLE_SPEED_EPS` 2e-2 m/s", "2e-2"),
        ("ε=0.020 m/s", "0.020"),
    ],
)
def test_idle_speed_eps_mismatch_is_error(tmp_path, monkeypatch, line, doc_value):
    _write_doc(tmp_path, monkeypatch, line)
    findings = cc.check_idle_speed_eps(_src(), None)

    assert len(findings) == 1
    f = findings[0]
    assert f.level == cc.ERROR
    assert f.rule == "A4-idle-speed-eps"
    assert f.file.endswith("12-infra.md") and f.line == 1
    assert doc_value in f.message  # the doc's value
    assert "0.01" in f.message  # the frozen value
    assert "docs を凍結契約に合わせる" in f.message


def test_checks_follow_the_frozen_value_not_a_hardcoded_one(tmp_path, monkeypatch):
    """The comparison must read ``Sources`` — re-freezing ε to 0.02 flips the verdict."""
    _write_doc(tmp_path, monkeypatch, "ε=0.02 m/s")
    assert cc.check_idle_speed_eps(_src(eps=0.01), None) != []
    assert cc.check_idle_speed_eps(_src(eps=0.02), None) == []


def test_one_finding_per_line_even_with_two_forms(tmp_path, monkeypatch):
    _write_doc(tmp_path, monkeypatch, "`IDLE_SPEED_EPS = 0.02`（ε=0.02 m/s）")
    assert len(cc.check_idle_speed_eps(_src(), None)) == 1


def test_a_second_copy_on_the_same_line_is_still_compared(tmp_path, monkeypatch):
    """A line may re-type the constant twice — a RIGHT copy must not hide a WRONG one.

    Contract: the line asserts both 0.3 (== frozen, silent) and 0.7 (!= frozen, ERROR).
    Comparing only the first occurrence would let a cap revision land silently.
    """
    _write_doc(
        tmp_path,
        monkeypatch,
        "ハード cap は `MAX_LINEAR_VELOCITY = 0.3 m/s`、M1 では `MAX_LINEAR_VELOCITY = 0.7 m/s` へ",
    )
    findings = cc.check_speed_cap(_src(), None)

    assert len(findings) == 1  # still one finding per line
    assert findings[0].level == cc.ERROR
    assert "0.7" in findings[0].message  # the MISMATCHING copy, not the matching one


def test_only_filter_limits_the_scan(tmp_path, monkeypatch):
    """`only` (per-file hook / pre-commit mode) must scope BOTH checks to the listed files."""
    clean = _write_doc(tmp_path, monkeypatch, "`MAX_LINEAR_VELOCITY = 0.3 m/s`", name="12-a.md")
    dirty = _write_doc(tmp_path, monkeypatch, "`MAX_LINEAR_VELOCITY = 0.25 m/s`", name="13-b.md")

    assert cc.check_speed_cap(_src(), [clean]) == []  # the bad file is not in `only`
    assert len(cc.check_speed_cap(_src(), [dirty])) == 1
    assert len(cc.check_speed_cap(_src(), None)) == 1  # full scan sees it


def test_checks_are_not_no_ops_on_the_live_corpus():
    """Guard against a silently non-matching regex: asking with a WRONG frozen value must
    light up the real docs sites that DO name each constant (19 / 4 at the time of #652)."""
    cap_hits = cc._const_copy_findings("MAX_LINEAR_VELOCITY", -1.0, "A3", (), "x", None)
    eps_hits = cc._const_copy_findings(
        "IDLE_SPEED_EPS", -1.0, "A4", (cc._EPS_SYMBOL_PAT,), "x", None
    )
    stopped = (
        "{} regex stopped matching the live docs corpus (or the {} sites were rewritten) "
        "— re-measure and update this floor"
    )
    assert len(cap_hits) >= 5, stopped.format("A3", "cap")
    assert len(eps_hits) >= 3, stopped.format("A4", "ε")
