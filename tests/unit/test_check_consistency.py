"""Unit tests for ``scripts/check_consistency.py`` — focused on the B4 cross-file
``doc:line`` reference drift check added for #177 (a regression guard for the #165
class of bug, where inserting lines into a doc silently broke every ``docNN:LINE``
reference pointing past the insertion).

Pure-logic tests (no ROS / hardware) → ``unit`` marker, NOT ``safety``: this check is
governance tooling, not an Emergency Guardian / Policy Gate / speed-clamp invariant.

The checker is loaded by file path (it is a script, not a package module) and its
module-level ``ROOT`` / ``DOCS`` globals are monkeypatched onto a synthetic temp tree,
so the tests are hermetic and do not depend on the live repo corpus.
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
