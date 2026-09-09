"""web_bridge runtime settings — config resolution with fail-open defaults (doc22 §16:345).

Resolved from ``warehouse_interfaces.config.load_config()`` (base + overlay + env, doc19).
The ``web_bridge`` block is additive: ``port``/``snapshot_hz`` live in base, ``host``/
``allowed_origins``/``recordings_dir`` in the per-env overlay (doc22:345-346). Every field
**fail-opens to a code default** so the node starts even before the base.yaml block lands —
the same precedent as the Nav2 Bridge's ``DEFAULT_HOST``/``DEFAULT_PORT`` (nav2_bridge.py:40).

``browser_config`` projects the **browser-facing subset only** (doc22:166-172): the shared
token is never read here and never returned (doc22:244,:254 / safety.md).

One of those browser-facing values is the **Langfuse project base URL** that turns a derived
``trace_id`` (doc22:194) into a clickable deep-link. It is config, not build-time env, because
doc22:332 requires exactly that ("env 値は build に焼かず ``GET /config`` で runtime 取得") so a
single static-export bundle serves dev and prod. :func:`normalize_langfuse_base_url` is the
gate: the value ends up in an ``href`` in the operator's browser, so a non-``http(s)`` scheme
(``javascript:`` / ``data:``) or embedded credentials must never reach the wire — see its
docstring for the full rule.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from urllib.parse import urlsplit

log = logging.getLogger(__name__)

# Fail-open defaults (overridable by config; the base.yaml web_bridge block is authoritative).
DEFAULT_HOST = "127.0.0.1"  # loopback unless an overlay opts into LAN (doc22:254)
DEFAULT_PORT = 8646  # §17 port registry (doc22:359)
DEFAULT_SNAPSHOT_HZ = 2.0  # coalesce target (doc22:206)
DEFAULT_MAX_CLIENTS = 8  # WS connection cap (doc22:233)
DEFAULT_CLIENT_QUEUE_MAX = 256  # per-client bounded queue depth (doc22:230)
DEFAULT_RECONNECT_MAX = 10  # per-IP accepts per window (reconnect-storm cap, doc22:235)
DEFAULT_RECONNECT_WINDOW_S = 10.0
# Dev fail-open only. Prod MUST set an explicit SSD path via overlay and NEVER reuse the
# tmpfs runtime_dir /run/warehouse (doc22:216,:220). The dev overlay sets this explicitly.
DEFAULT_RECORDINGS_DIR = "/tmp/warehouse/recordings"
# web/console static export out/ dir (doc22:341,:246). Empty until S3 builds it / config sets
# it → the StaticFiles mount is simply skipped, so the gateway runs API-only before the SPA.
DEFAULT_STATIC_DIR = ""
# Langfuse PROJECT-scoped console base (e.g. https://cloud.langfuse.com/project/<id>) so that
# `<base>/traces/<trace_id>` is the canonical trace URL. Empty = no deep-link: the console shows
# the trace_id as copyable text instead — the same fail-open degrade a null trace_id gets
# (doc22:152,:194). Non-secret (it names a project, never a key), so it is an overlay config key
# beside host/allowed_origins/recordings_dir (doc22:346), NOT a `config/<env>/.env` secret.
DEFAULT_LANGFUSE_BASE_URL = ""

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}
# Only these two schemes may reach the browser's href (see normalize_langfuse_base_url).
_URL_SCHEMES = ("http", "https")
# The path segment that marks a PROJECT-scoped Langfuse console base; its absence only warns.
_PROJECT_SEGMENT = "/project/"
_CONTROL_CHARS = frozenset(chr(c) for c in list(range(0x20)) + [0x7F])
# Served on every (unauthenticated) GET /config, so bound it like the other caps in this module.
MAX_LANGFUSE_BASE_URL_LEN = 2048


@dataclass(frozen=True)
class WebBridgeSettings:
    host: str
    port: int
    snapshot_hz: float
    recordings_dir: str
    static_dir: str
    max_clients: int
    client_queue_max: int
    reconnect_max_per_window: int
    reconnect_window_s: float
    allowed_origins: tuple[str, ...]
    token_required: bool
    langfuse_base_url: str = DEFAULT_LANGFUSE_BASE_URL

    @property
    def lan(self) -> bool:
        """True when bound to a non-loopback address (LAN-exposed, doc22:170,:254)."""
        return self.host not in _LOOPBACK


def normalize_langfuse_base_url(raw: object) -> str:
    """Vet the operator-supplied Langfuse base URL; return ``""`` (= no deep-link) if unusable.

    This value is the ONLY ``/config`` field the console feeds into an ``href``
    (``TraceLink.tsx``), so it is the one field where a bad config value is not merely a broken
    link. Every rule fail-CLOSES to ``""`` **and logs** (a silent empty knob is a support call;
    a silently accepted bad one is worse):

    1. **scheme allowlist** — ``http`` or ``https`` only. This rejects ``javascript:``/``data:``
       URLs, which React renders into an ``href`` without blocking them, and protocol-relative
       ``//evil.example``, which would silently inherit the page's scheme.
    2. **the host must be what it looks like** — parsed with :func:`urllib.parse.urlsplit`, i.e.
       the same split the browser will make, and then required to be **ASCII**. A hand-rolled
       ``split("/")`` is not enough: browsers apply UTS-46, so ``https://lf.example。evil.example``
       (U+3002, and likewise U+FF0E / U+FF61) *reads* as ``lf.example`` in a config review but
       resolves to the host ``lf.example.evil.example``. Backslashes are rejected for the same
       reason — a browser normalises a backslash to ``/``, so a value using one in place of the
       authority separator would leave the intended origin while still looking like a path.
    3. **no embedded credentials** — a ``user:pass@host`` authority would put a secret on the
       wire for every browser that calls ``/config``, which doc22:254 forbids outright.
    4. **no whitespace or control characters** — a value carrying a newline or NUL is a mangled
       config; a newline in particular must never be echoed into a header-ish context.
    5. **no query or fragment, and a length cap** — ``?``/``#`` would swallow the id that the
       console appends (``…/phish#`` turns ``/traces/<id>`` into a fragment), so rejecting them
       is what actually pins the ``{base}/traces/{id}`` shape.

    A value that passes but is not PROJECT-scoped (``…/project/<id>``) is **accepted with a
    warning**, not rejected: ``https://cloud.langfuse.com`` is exactly what ``LANGFUSE_HOST``
    holds (doc19:79), so it is the likeliest operator mistake, but a self-hosted Langfuse may
    legitimately use another layout — warn, and let the operator decide.

    Also strips surrounding whitespace and **trailing slashes**, so the console's
    ``f"{base}/traces/{trace_id}"`` cannot produce a doubled ``//``. Pure and total: it never
    raises, for any YAML scalar, so a hand-edited overlay cannot stop the gateway from starting.
    (That is deliberately stronger than this module's numeric fields, where a non-numeric
    ``port``/``snapshot_hz`` still raises out of :func:`resolve_settings` — hardening those is a
    separate slice, so do not read this docstring as describing the module as a whole.)
    """
    if not isinstance(raw, str):
        # None (key absent) is the normal case and not worth a warning; a wrong TYPE is.
        if raw is not None:
            log.warning("web_bridge.langfuse_base_url is not a string (%r); ignoring", type(raw))
        return DEFAULT_LANGFUSE_BASE_URL
    value = raw.strip()
    if not value:
        return DEFAULT_LANGFUSE_BASE_URL
    if len(value) > MAX_LANGFUSE_BASE_URL_LEN:
        # It is served on every unauthenticated GET /config; cap it like the other bounds here.
        log.warning(
            "web_bridge.langfuse_base_url is longer than %d characters; ignoring",
            MAX_LANGFUSE_BASE_URL_LEN,
        )
        return DEFAULT_LANGFUSE_BASE_URL
    if any(ch.isspace() or ch in _CONTROL_CHARS for ch in value):
        log.warning("web_bridge.langfuse_base_url contains whitespace/control chars; ignoring")
        return DEFAULT_LANGFUSE_BASE_URL
    if "\\" in value:
        log.warning("web_bridge.langfuse_base_url contains a backslash; ignoring")
        return DEFAULT_LANGFUSE_BASE_URL
    if "?" in value or "#" in value:
        log.warning(
            "web_bridge.langfuse_base_url must be a plain base URL with no query or fragment "
            "(the console appends /traces/<trace_id> to it); ignoring"
        )
        return DEFAULT_LANGFUSE_BASE_URL
    try:
        parts = urlsplit(value)
        scheme, host, user, password = parts.scheme, parts.hostname, parts.username, parts.password
    except ValueError:
        # urlsplit DOES raise (bad IPv6 literal, NFKC-invalid netloc). Stay total.
        log.warning("web_bridge.langfuse_base_url is not a parsable URL; ignoring")
        return DEFAULT_LANGFUSE_BASE_URL
    if scheme.lower() not in _URL_SCHEMES:
        log.warning(
            "web_bridge.langfuse_base_url must start with http:// or https://; ignoring the "
            "configured value (no Langfuse deep-link this run)"
        )
        return DEFAULT_LANGFUSE_BASE_URL
    if not host:
        log.warning("web_bridge.langfuse_base_url has no host; ignoring")
        return DEFAULT_LANGFUSE_BASE_URL
    if not host.isascii():
        # See rule 2: a non-ASCII host does not resolve to the host a reviewer reads.
        log.warning("web_bridge.langfuse_base_url has a non-ASCII host; ignoring")
        return DEFAULT_LANGFUSE_BASE_URL
    if user or password:
        # Never echo credentials to every browser that GETs /config (doc22:254).
        log.warning("web_bridge.langfuse_base_url embeds credentials; ignoring")
        return DEFAULT_LANGFUSE_BASE_URL
    normalized = value.rstrip("/")
    if _PROJECT_SEGMENT not in normalized:
        # Accepted, but almost certainly the API host (LANGFUSE_HOST, doc19:79) rather than the
        # project console base — the resulting deep-link would 404. Say so once, at startup.
        log.warning(
            "web_bridge.langfuse_base_url does not contain %r; a Langfuse deep-link needs the "
            "PROJECT-scoped console base (e.g. https://cloud.langfuse.com/project/<id>), not the "
            "API host — trace links will probably 404",
            _PROJECT_SEGMENT,
        )
    return normalized


def resolve_settings(config: dict, *, token: str | None = None) -> WebBridgeSettings:
    """Build :class:`WebBridgeSettings` from a loaded config dict (fail-open per field).

    ``token`` is the resolved ``WEB_BRIDGE_TOKEN`` (or ``None``); only its presence is kept
    (as ``token_required``) — the secret value is never stored here (doc22:254).
    """
    wb = config.get("web_bridge") or {}
    return WebBridgeSettings(
        host=str(wb.get("host", DEFAULT_HOST)),
        port=int(wb.get("port", DEFAULT_PORT)),
        snapshot_hz=float(wb.get("snapshot_hz", DEFAULT_SNAPSHOT_HZ)),
        recordings_dir=str(wb.get("recordings_dir", DEFAULT_RECORDINGS_DIR)),
        static_dir=str(wb.get("static_dir", DEFAULT_STATIC_DIR)),
        # clamp to >=1 so a stray ``0`` never disables the bounded-queue / client cap that
        # #187 depends on (asyncio.Queue(maxsize<=0) is UNBOUNDED — doc22:230-233).
        max_clients=max(1, int(wb.get("max_clients", DEFAULT_MAX_CLIENTS))),
        client_queue_max=max(1, int(wb.get("client_queue_max", DEFAULT_CLIENT_QUEUE_MAX))),
        reconnect_max_per_window=max(
            1, int(wb.get("reconnect_max_per_window", DEFAULT_RECONNECT_MAX))
        ),
        reconnect_window_s=float(wb.get("reconnect_window_s", DEFAULT_RECONNECT_WINDOW_S)),
        allowed_origins=tuple(wb.get("allowed_origins") or ()),
        token_required=bool(token),
        langfuse_base_url=normalize_langfuse_base_url(wb.get("langfuse_base_url")),
    )


def browser_config(settings: WebBridgeSettings, mode: str) -> dict:
    """The exact JSON ``GET /config`` returns — browser-facing values ONLY (doc22:166-172).

    Carries no secret and no server internals (host/port/recordings_dir): the SPA learns
    only the ws path, the run mode (for per-mode gating §12.1), whether the gateway is
    LAN-exposed, whether a token is required, and the (non-secret, already vetted) Langfuse
    project base URL for trace deep-links. The token itself is never returned (doc22:244,:254).
    """
    return {
        "ws_path": "/ws",  # same-origin relative path (doc22:168)
        "mode": mode,  # none | simple | open-rmf (doc22:169)
        "lan": settings.lan,  # doc22:170
        "token_required": settings.token_required,
        # THIS gateway always sends the key (``""`` when unconfigured) so the SPA has one shape
        # to branch on. The SPA still types it optional, because it is a separately-deployed
        # static bundle that may meet an older gateway (web/console/lib/types.ts). Vetted at
        # resolve time, not here, so an unusable value never reaches a browser href (doc22:171).
        "langfuse_base_url": settings.langfuse_base_url,
    }
