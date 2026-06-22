#!/usr/bin/env python3
"""Probe Gemini Robotics-ER availability without printing API secrets."""

from __future__ import annotations

import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple


DEFAULT_MODEL = "gemini-robotics-er-1.6-preview"
DEFAULT_PROMPT = (
    "You are an access probe. Return JSON only with keys status, model, and note. "
    "Set status to ok if you can respond."
)
DEFAULT_IMAGE_PROMPT = (
    "Point to no more than 10 visible items in the image. Return JSON only in the "
    'format [{"point": [y, x], "label": "name"}]. Coordinates are normalized '
    "to 0-1000. If no items are visible, return []."
)


def _api_key() -> Optional[str]:
    return os.environ.get("GOOGLE_API_KEY") or os.environ.get("GEMINI_API_KEY")


def _mime_type(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime in {"image/png", "image/jpeg", "image/webp"}:
        return mime
    raise SystemExit(
        f"Unsupported image type for {path}. Use PNG, JPEG, or WebP for this probe."
    )


def _contents(args: argparse.Namespace) -> list[dict[str, object]]:
    parts: list[dict[str, object]] = []
    if args.image:
        image_path = Path(args.image)
        image_bytes = image_path.read_bytes()
        parts.append(
            {
                "inlineData": {
                    "mimeType": _mime_type(image_path),
                    "data": base64.b64encode(image_bytes).decode("ascii"),
                }
            }
        )
        parts.append({"text": args.prompt or DEFAULT_IMAGE_PROMPT})
    else:
        parts.append({"text": args.prompt or DEFAULT_PROMPT})
    return [{"role": "user", "parts": parts}]


def _payload(args: argparse.Namespace) -> bytes:
    generation_config: dict[str, object] = {"temperature": args.temperature}
    if args.thinking_budget is not None:
        generation_config["thinkingConfig"] = {"thinkingBudget": args.thinking_budget}
    return json.dumps(
        {
            "contents": _contents(args),
            "generationConfig": generation_config,
        },
        separators=(",", ":"),
    ).encode("utf-8")


def _request(args: argparse.Namespace, key: str) -> Tuple[int, str]:
    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{args.model}:generateContent"
    )
    request = urllib.request.Request(
        url,
        data=_payload(args),
        headers={
            "Content-Type": "application/json",
            "x-goog-api-key": key,
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=args.timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")


def _redact_response(body: str) -> str:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        return body
    _redact_json(payload)
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _redact_json(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if key == "thoughtSignature":
                del value[key]
            else:
                _redact_json(value[key])
    elif isinstance(value, list):
        for item in value:
            _redact_json(item)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Call Gemini Robotics-ER generateContent using GEMINI_API_KEY."
    )
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--image", help="Optional PNG/JPEG/WebP image for spatial probe.")
    parser.add_argument("--prompt", help="Override the default probe prompt.")
    parser.add_argument("--thinking-budget", type=int, default=0)
    parser.add_argument("--temperature", type=float, default=0.2)
    parser.add_argument("--timeout", type=float, default=45.0)
    parser.add_argument("--max-output-chars", type=int, default=4000)
    args = parser.parse_args()

    key = _api_key()
    if not key:
        print(
            "Missing GEMINI_API_KEY or GOOGLE_API_KEY. Export one before running live probe.",
            file=sys.stderr,
        )
        return 2

    status, body = _request(args, key)
    print(f"HTTP {status}")
    print(_redact_response(body)[: args.max_output_chars])
    if status < 200 or status >= 300:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
