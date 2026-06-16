"""Fail-open Langfuse **v4** score sink — the domain-free emit seam (doc21 §3/§4).

A caller self-sends scores (any name / value / data type) keyed by a 32-hex ``trace_id`` it
derives from :mod:`eval_sdk.seed`. The Langfuse Python SDK is **v4 (>=4.7,<5)**: the v2
``score()`` was removed, so the call is ``create_score(trace_id, name, value, data_type,
metadata)`` followed by ``flush()`` (doc21 §12.4).

Design (doc21 §4 背骨, verbatim-preserved):

* **fail-open** — a missing SDK / missing keys / network errors are swallowed; the caller's
  loop never breaks. ``flush()`` is best-effort.
* **lazy/optional import** — ``langfuse`` is imported on demand (an optional pip extra) so the
  package builds and tests run with langfuse absent.
* **trace_id-gated** — every send no-ops (returns ``False``) without a usable trace_id.

This core carries **no domain vocabulary**: score names, data-type meaning, the credential env
var *names*, and any score-name fallback policy are the consumer's (doc21 §3 (c)). If the
pinned SDK rejects ``metadata=`` / ``data_type=`` the call retries with the minimal signature;
the fallback score *name* is produced by :meth:`FailOpenScoreSink._fallback_name`, an
overridable hook a domain subclass uses to embed a label in the name (e.g. ``result_bot1``).

Pure stdlib at import time (no rclpy, no hard ``langfuse`` dep) → unit-testable per doc16 §11.
"""

import logging
import os

from eval_sdk.seed import normalize_trace_id

log = logging.getLogger(__name__)

# Langfuse v4 score data types (doc21 §3 / doc08 §比較指標). Plain strings in Phase 1; a typed
# ScoreSpec/registry is a later additive contract phase (doc21 §10 Phase 2), not invented here.
DATA_TYPE_CATEGORICAL = "CATEGORICAL"
DATA_TYPE_NUMERIC = "NUMERIC"
DATA_TYPE_BOOLEAN = "BOOLEAN"


def build_client_from_env(public_key_env: str, secret_key_env: str) -> object | None:
    """Best-effort construct a v4 Langfuse client via ``get_client()``; ``None`` if unavailable.

    Gated on BOTH credential env vars being present (their *names* are the caller's — the SDK
    is domain-free). With either absent, or langfuse not installed, or client init failing, it
    returns ``None`` so the owning sink stays disabled (fail-open). Lazy/optional import.
    """
    if not (os.environ.get(public_key_env) and os.environ.get(secret_key_env)):
        return None
    try:
        from langfuse import get_client  # v4 entrypoint; lazy/optional (pip extra)
    except ImportError:
        log.info("langfuse SDK not installed; score-send disabled (fail-open)")
        return None
    try:
        return get_client()
    except Exception as exc:
        log.warning("langfuse client init failed; score-send disabled (fail-open): %s", exc)
        return None


class FailOpenScoreSink:
    """Sends scores via Langfuse v4, or no-ops when it cannot (fail-open).

    Construct with an explicit ``client`` (e.g. a fake in tests) or build one from env via
    :meth:`from_env`. With no client it is **disabled** — every send returns ``False`` without
    raising.
    """

    def __init__(self, client: object | None = None, *, enabled: bool | None = None) -> None:
        self._client = client
        self._enabled = self._client is not None if enabled is None else enabled

    @classmethod
    def from_env(
        cls,
        *,
        public_key_env: str,
        secret_key_env: str,
        enabled: bool | None = None,
    ) -> "FailOpenScoreSink":
        """Build a sink whose client is constructed from the named credential env vars.

        The env var *names* are supplied by the caller (domain-free); absent creds → a disabled
        sink (fail-open), never a raise. This is the "接続するだけ" entry point of doc21 §5.
        """
        return cls(build_client_from_env(public_key_env, secret_key_env), enabled=enabled)

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def _fallback_name(self, name: str, metadata: dict | None) -> str:
        """Score name used on the minimal-signature retry. Identity by default.

        Overridable hook: a domain subclass embeds a label in the name when the pinned SDK
        rejects ``metadata=`` (Langfuse scores carry no tags) — e.g. ``result_bot1``.
        """
        return name

    def score(
        self,
        trace_id: str | None,
        name: str,
        value: object,
        data_type: str,
        metadata: dict | None = None,
    ) -> bool:
        """``create_score(...)`` if possible; return whether a score was sent (v4).

        No-op (``False``) when disabled or ``trace_id`` is falsy/invalid. On ``TypeError``
        (pinned SDK lacks ``metadata=`` / ``data_type=``) retries the minimal signature with
        :meth:`_fallback_name`. Any other SDK error is swallowed (fail-open).
        """
        if not self.enabled or not trace_id:
            return False
        try:
            tid = normalize_trace_id(trace_id)
        except ValueError:
            log.warning("invalid trace_id %r; score %s skipped", trace_id, name)
            return False
        try:
            self._client.create_score(
                trace_id=tid, name=name, value=value, data_type=data_type, metadata=metadata
            )
        except TypeError:
            try:
                self._client.create_score(
                    trace_id=tid, name=self._fallback_name(name, metadata), value=value
                )
            except Exception as exc:
                log.warning("create_score(%s) fallback failed (fail-open): %s", name, exc)
                return False
            return True
        except Exception as exc:
            log.warning("create_score(%s) failed (fail-open): %s", name, exc)
            return False
        return True

    def flush(self) -> None:
        """Flush buffered scores (a short-lived scorer must flush before exit)."""
        if not self.enabled:
            return
        try:
            self._client.flush()
        except Exception as exc:
            log.warning("langfuse flush failed (fail-open): %s", exc)
