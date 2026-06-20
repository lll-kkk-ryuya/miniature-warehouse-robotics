"""web_bridge runtime settings — config resolution with fail-open defaults (doc22 §16:345).

Resolved from ``warehouse_interfaces.config.load_config()`` (base + overlay + env, doc19).
The ``web_bridge`` block is additive: ``port``/``snapshot_hz`` live in base, ``host``/
``allowed_origins``/``recordings_dir`` in the per-env overlay (doc22:345-346). Every field
**fail-opens to a code default** so the node starts even before the base.yaml block lands —
the same precedent as the Nav2 Bridge's ``DEFAULT_HOST``/``DEFAULT_PORT`` (nav2_bridge.py:40).

``browser_config`` projects the **browser-facing subset only** (doc22:166-172): the shared
token is never read here and never returned (doc22:244,:254 / safety.md).
"""

from __future__ import annotations

from dataclasses import dataclass

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

_LOOPBACK = {"127.0.0.1", "::1", "localhost"}


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

    @property
    def lan(self) -> bool:
        """True when bound to a non-loopback address (LAN-exposed, doc22:171,:254)."""
        return self.host not in _LOOPBACK


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
    )


def browser_config(settings: WebBridgeSettings, mode: str) -> dict:
    """The exact JSON ``GET /config`` returns — browser-facing values ONLY (doc22:166-172).

    Carries no secret and no server internals (host/port/recordings_dir): the SPA learns
    only the ws path, the run mode (for per-mode gating §12.1), whether the gateway is
    LAN-exposed, and whether a token is required. The token itself is never returned
    (doc22:244,:254).
    """
    return {
        "ws_path": "/ws",  # same-origin relative path (doc22:168)
        "mode": mode,  # none | simple | open-rmf (doc22:170)
        "lan": settings.lan,
        "token_required": settings.token_required,
    }
