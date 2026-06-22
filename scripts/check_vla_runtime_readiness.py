#!/usr/bin/env python3
"""Check local readiness for offline VLA runtime probes.

This script does not install packages or download model weights. It reports whether the
current machine looks suitable for OpenVLA / Isaac GR00T open-loop inference.
"""

from __future__ import annotations

import argparse
import json
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from typing import Optional


OPENVLA_MIN_VRAM_GB = 16
GROOT_INFERENCE_MIN_VRAM_GB = 16
GROOT_FINETUNE_RECOMMENDED_VRAM_GB = 40


@dataclass(frozen=True)
class Check:
    name: str
    status: str
    detail: str


def _which(name: str) -> Optional[str]:
    return shutil.which(name)


def _run(cmd: list[str]) -> tuple[int, str]:
    try:
        proc = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)
    return proc.returncode, (proc.stdout + proc.stderr).strip()


def _nvidia_vram_gb() -> Optional[float]:
    if not _which("nvidia-smi"):
        return None
    code, output = _run(
        [
            "nvidia-smi",
            "--query-gpu=memory.total",
            "--format=csv,noheader,nounits",
        ]
    )
    if code != 0:
        return None
    values = []
    for line in output.splitlines():
        try:
            values.append(float(line.strip()) / 1024.0)
        except ValueError:
            continue
    return max(values) if values else None


def _tool_check(name: str, why: str, required: bool = True) -> Check:
    path = _which(name)
    if path:
        return Check(name, "ok", f"{path} ({why})")
    status = "missing" if required else "optional-missing"
    return Check(name, status, why)


def _hardware_checks() -> list[Check]:
    checks = [
        Check("platform", "info", f"{platform.system()} {platform.machine()}"),
        Check("python", "info", sys.version.split()[0]),
    ]
    vram = _nvidia_vram_gb()
    if vram is None:
        checks.append(
            Check(
                "nvidia_gpu",
                "missing",
                "nvidia-smi not available; OpenVLA/GR00T inference should run on a CUDA GPU server.",
            )
        )
    elif vram >= GROOT_FINETUNE_RECOMMENDED_VRAM_GB:
        checks.append(Check("nvidia_gpu", "ok", f"{vram:.1f} GB VRAM"))
    elif vram >= GROOT_INFERENCE_MIN_VRAM_GB:
        checks.append(
            Check(
                "nvidia_gpu",
                "ok-inference-only",
                f"{vram:.1f} GB VRAM; enough for inference-class probes, not fine-tuning.",
            )
        )
    else:
        checks.append(
            Check(
                "nvidia_gpu",
                "insufficient",
                f"{vram:.1f} GB VRAM; target at least {OPENVLA_MIN_VRAM_GB} GB.",
            )
        )
    return checks


def _openvla_checks() -> list[Check]:
    return [
        _tool_check("python3.10", "OpenVLA docs use Python 3.10."),
        _tool_check("git", "clone openvla/openvla."),
        _tool_check("git-lfs", "download large Hugging Face checkpoints.", required=False),
        _tool_check("conda", "official setup path uses a conda env.", required=False),
    ]


def _groot_checks() -> list[Check]:
    return [
        _tool_check("python3.10", "GR00T dGPU/Orin path uses Python 3.10."),
        _tool_check("uv", "official Isaac-GR00T setup uses uv."),
        _tool_check("git", "clone NVIDIA/Isaac-GR00T."),
        _tool_check("git-lfs", "download demo data/model assets."),
        _tool_check("ffmpeg", "GR00T video backend requires FFmpeg."),
        _tool_check("docker", "recommended isolated setup path.", required=False),
    ]


def _print_text(checks: list[Check]) -> None:
    width = max(len(check.name) for check in checks)
    for check in checks:
        print(f"{check.name:<{width}}  {check.status:<18}  {check.detail}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--profile",
        choices=("all", "openvla", "groot"),
        default="all",
        help="Runtime stack to check.",
    )
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when required runtime pieces are missing.",
    )
    args = parser.parse_args()

    checks = _hardware_checks()
    if args.profile in {"all", "openvla"}:
        checks.extend(_openvla_checks())
    if args.profile in {"all", "groot"}:
        checks.extend(_groot_checks())

    if args.json:
        print(json.dumps([check.__dict__ for check in checks], indent=2))
    else:
        _print_text(checks)

    bad = {"missing", "insufficient"}
    if args.strict and any(check.status in bad for check in checks):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
