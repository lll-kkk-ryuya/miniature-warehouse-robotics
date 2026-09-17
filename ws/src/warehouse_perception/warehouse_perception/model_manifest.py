"""09_Runtime_and_Models — loader / verifier / CLI for the model manifest artifact.

Layer note (`.claude/rules/layer-annotation.md`): 09_Runtime_and_Models is 基盤 —
it is deliberately NOT assigned to a single L0-L4 layer, and the repo-wide version
LIST stays in 00 (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:335``,
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:204``). This module is an
OFFLINE tool: it reads a YAML file and validates it against the frozen contract. It
opens no ROS context, publishes nothing, and never runs while the vehicle drives
(``docs/mode-outdoor/02-architecture-split-orin-pc-cloud.md:161``).

Source of truth:

* ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:204`` (追補 ② §1 09 行) —
  the manifest is ONE 採用単位 owned by 04 as an artifact, and the engine is burned
  ON THE BOARD.
* ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:385`` (追補 ③ final row) —
  the item list the manifest must carry.
* ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:459`` (追補 ④ §1-5) — the
  frozen field/fail-direction table this module validates against. The types live in
  ``warehouse_interfaces.perception``; nothing here re-derives them.

Deliberately ABSENT (not an omission — a boundary):

* No TensorRT engine is built or read. ``OQ-OD4U``
  (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:358``) pins an engine to the
  GPU arch and TRT version, so it must be burned on the board; this host-side tool can
  only RECORD that fact (``ModelManifest.engine_built_on_board``).
* No weights are downloaded and no inference backend is imported. ``torch`` /
  ``tensorrt`` / ``numpy`` are not available in CI and are not dependencies here
  (pure python + PyYAML + the frozen contract).

Fail direction: LOUD. ``load_manifest`` lets ``pydantic.ValidationError`` propagate
unchanged, because the failure of an offline adoption-record tool must stop the
operator, not degrade into a default. That is the opposite of the runtime rule in
``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:482`` (追補 ④ §2-6), which
forbids carrying such an exception INTO the safety loop — there is no safety loop here.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from collections.abc import Sequence
from pathlib import Path

import yaml
from warehouse_interfaces.perception import ModelManifest

# Read the weights in chunks: an adopted model is hundreds of MB and the host running
# this tool is the companion Mac, not a build machine.
_HASH_CHUNK_BYTES = 1 << 20


def load_manifest(path: str | Path) -> ModelManifest:
    """Load one manifest YAML file and validate it against the frozen contract.

    Args:
        path: the manifest YAML file.

    Returns:
        The validated :class:`warehouse_interfaces.perception.ModelManifest`.

    Raises:
        OSError: the file cannot be read.
        yaml.YAMLError: the file is not valid YAML.
        ValueError: the document is not a YAML mapping (a list / scalar / empty file
            cannot carry named manifest fields, and ``model_validate`` would report
            that as a type error far from the cause).
        pydantic.ValidationError: any field violates 追補 ④ §1-5 — propagated
            UNCHANGED so the operator sees which field and why.
    """
    text = Path(path).read_text(encoding="utf-8")
    document = yaml.safe_load(text)
    if not isinstance(document, dict):
        kind = type(document).__name__
        raise ValueError(f"{path}: manifest must be a YAML mapping, got {kind}")
    return ModelManifest.model_validate(document)


def sha256_of_file(path: str | Path) -> str:
    """Return the lowercase 64-hex sha256 digest of a file's bytes.

    This is the value ``sha256sum <file>`` prints, so a manifest's
    ``weights_sha256`` can be produced and re-checked with a standard tool. The
    algorithm is PROVISIONAL at the contract level: the docs require a "重み hash"
    without naming one and v0 pins sha256 through the FIELD NAME
    (``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:496`` ``OQ-OD4Y-k``).
    """
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        while True:
            chunk = handle.read(_HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def verify_weights(manifest: ModelManifest, weights_path: str | Path) -> bool:
    """Whether the file at ``weights_path`` hashes to ``manifest.weights_sha256``.

    Case-insensitive on the hex digits: the frozen contract validates
    ``^[0-9a-fA-F]{64}$``
    (``ws/src/warehouse_interfaces/warehouse_interfaces/perception.py:48``, the field
    table being ``docs/mode-outdoor/04-perception-sidewalk-and-signals.md:459``) so a
    manifest may legitimately carry an uppercase digest, while :func:`sha256_of_file`
    emits lowercase.

    Raises:
        OSError: the weights file cannot be read — a MISSING file is not "mismatch".
            Returning ``False`` for an unreadable file would let "I could not check"
            wear the same result as "I checked and it differs".
    """
    return sha256_of_file(weights_path).lower() == manifest.weights_sha256.lower()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="perception_manifest",
        description=(
            "Validate a 09_Runtime model manifest (docs/mode-outdoor/04 追補 ④). "
            "Offline only: no engine is built, no weights are downloaded."
        ),
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    validate = subparsers.add_parser(
        "validate",
        help="validate a manifest YAML file; optionally check the weights digest",
    )
    validate.add_argument("manifest", help="path to the manifest YAML file")
    validate.add_argument(
        "--weights",
        default=None,
        help="weights file whose sha256 must equal weights_sha256",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point (``perception_manifest validate <yaml> [--weights <file>]``).

    Returns:
        ``0`` when the manifest validates (and, with ``--weights``, the digest
        matches); ``1`` for every failure. Non-zero is the whole point: this runs in
        an operator's shell and in a future regression job, so a broken adoption
        record must not exit green.
    """
    args = _build_parser().parse_args(argv)
    try:
        manifest = load_manifest(args.manifest)
    # CLI boundary: report the cause on stderr rather than dumping a traceback. The
    # library call (load_manifest) still raises; only the shell wrapper flattens it.
    except Exception as error:
        print(f"INVALID {args.manifest}: {error}", file=sys.stderr)
        return 1

    print(
        f"OK {args.manifest}: {manifest.model_name} "
        f"(license={manifest.license}, dataset={manifest.evaluation.dataset_id}, "
        f"engine_built_on_board={manifest.engine_built_on_board})"
    )
    if args.weights is None:
        return 0

    try:
        matched = verify_weights(manifest, args.weights)
    except OSError as error:
        print(f"UNREADABLE {args.weights}: {error}", file=sys.stderr)
        return 1
    if not matched:
        print(
            f"WEIGHTS MISMATCH {args.weights}: expected {manifest.weights_sha256}",
            file=sys.stderr,
        )
        return 1
    print(f"OK weights {args.weights}: sha256 matches")
    return 0


if __name__ == "__main__":  # pragma: no cover - exercised through main(argv)
    raise SystemExit(main())
