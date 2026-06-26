"""STT (speech-to-text) seam for Mode X-ER — the OUT-OF-BAND transcript lane.

The audio critical path goes DIRECT to ER (ER 1.6 understands audio natively; Hermes can't carry
audio — 400). STT is NOT in that path: it runs in parallel for provenance/audit and to feed a
realtime UI (a Next.js client tails the transcript sink). See docs/mode-x-er/04 §2-3, 06 §5.

``Transcriber`` is the swappable seam. ``HermesTranscriber`` is the Hermes-side implementation: it
POSTs the audio to the Hermes dashboard's ``/api/audio/transcribe`` endpoint (verified 2026-06-26:
``{data_url, mime_type}`` -> faster-whisper/local -> ``{success, transcript, provider}``). The
blocking HTTP call runs in a thread so it never blocks the asyncio event loop / the ER lane.
"""

from __future__ import annotations

import asyncio
import base64
import json
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any, Protocol, runtime_checkable


@dataclass
class TranscriptResult:
    transcript: str
    provider: str | None = None
    success: bool = True
    error: str | None = None


@runtime_checkable
class Transcriber(Protocol):
    """Out-of-band STT seam. Implementations MUST NOT be put on the ER critical path."""

    async def transcribe(self, audio: bytes, *, mime: str = "audio/wav") -> TranscriptResult: ...


class HermesTranscriber:
    """Hermes-side STT via the dashboard ``/api/audio/transcribe`` (``hermes dashboard``).

    base_url e.g. ``http://127.0.0.1:9119``. Runs the blocking POST in a worker thread so it stays
    off the event loop. Fail-soft: any error returns ``success=False`` (the caller treats STT as
    best-effort provenance — it never blocks ER / motion).
    """

    def __init__(self, base_url: str, *, timeout: float = 30.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout

    async def transcribe(self, audio: bytes, *, mime: str = "audio/wav") -> TranscriptResult:
        data_url = f"data:{mime};base64," + base64.b64encode(audio).decode("ascii")
        body = json.dumps({"data_url": data_url, "mime_type": mime}).encode("utf-8")
        req = urllib.request.Request(
            self._base + "/api/audio/transcribe",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )

        def _call() -> dict[str, Any]:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8", "replace"))

        try:
            data = await asyncio.to_thread(_call)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            return TranscriptResult(transcript="", provider="hermes", success=False, error=str(exc))
        transcript = data.get("transcript")
        if transcript is None and isinstance(data.get("result"), dict):
            transcript = data["result"].get("transcript")
        return TranscriptResult(
            transcript=transcript or "",
            provider=data.get("provider", "hermes"),
            success=bool(transcript),
        )


class CallableTranscriber:
    """Wrap an ``async (audio, mime) -> TranscriptResult`` callable (offline tests / custom STT)."""

    def __init__(self, fn) -> None:
        self._fn = fn

    async def transcribe(self, audio: bytes, *, mime: str = "audio/wav") -> TranscriptResult:
        return await self._fn(audio, mime)
