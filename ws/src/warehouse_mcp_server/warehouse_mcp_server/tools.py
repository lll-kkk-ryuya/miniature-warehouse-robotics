"""The 7 Warehouse MCP tools — transport-agnostic core logic (doc15 §ツール定義).

Each tool is a plain ``async def`` on :class:`WarehouseTools`, fully unit-testable
with NO MCP library and NO network. ``server.py`` wraps these for the stdio wire.

Invariants enforced on EVERY tool:

* First line is ``await self._gen_checker.check(gen_id, idempotency_key)`` — a
  stale (B-3) call OR a replayed key (C, R-35) is rejected before any side effect
  (doc15 §2; order gen → idempotency → Policy Gate).
* Keyword-only args (after ``gen_id``) match ``warehouse_llm_bridge.action_map``
  EXACTLY (incl. the Bridge-injected ``idempotency_key``), so
  ``await getattr(tools, tc.tool)(**tc.args)`` works verbatim.
* Returns a dict with a ``status`` key: ``"ok" | "rejected" | "error"``.
* Every outcome is written to the audit log with ``result`` in
  ``{"executed", "rejected", "error"}``.

Divergence from doc15: ``dispatch_task`` takes ``pickup`` as optional
(``pickup=None``) because ``action_map`` never sends it (it carries only
``dropoff``); pickup-dependent checks run only when ``pickup is not None``. See
CLAUDE.md.

Pure Python — imports only ``warehouse_interfaces`` + sibling modules. No rclpy.
"""

import logging
from collections.abc import Callable
from typing import Any

from warehouse_interfaces.stores import FileStateStore, StateStore

from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker, GenCheckResult
from warehouse_mcp_server.nav2_client import Nav2Forwarder, plan_nav2_request
from warehouse_mcp_server.policy_gate import PolicyGate, freshness_from_config

log = logging.getLogger(__name__)

# Valid character-LLM negotiation starters (doc14 / doc15 ツール7).
NEGOTIATION_STARTERS = ("bot1", "bot2")
# Valid escalation_response actions (doc15 ツール6 / validate_escalation).
ESCALATION_ACTIONS = ("reassign", "cancel", "retry")
# The 7 callable MCP tool names — the wire allowlist. dispatch() refuses anything
# else, so a malformed/hostile tool name can never reach an arbitrary attribute.
TOOL_NAMES = frozenset(
    {
        "dispatch_task",
        "cancel_task",
        "get_fleet_status",
        "get_task_queue",
        "send_to_charging",
        "escalation_response",
        "start_negotiation",
    }
)


def _stale(gen_id: int) -> dict[str, Any]:
    """Build the canonical stale-generation rejection payload (doc15 §2)."""
    return {"status": "rejected", "reason": "stale_generation", "received_gen": gen_id}


def _gen_reject(res: GenCheckResult, gen_id: int, idempotency_key: str | None) -> dict[str, Any]:
    """Map a failed gen/idempotency check to its rejection payload (doc15 §2).

    ``"duplicate_command"`` (a replayed idempotency_key) vs ``"stale_generation"``.
    """
    if res.reason == "duplicate_command":
        return {
            "status": "rejected",
            "reason": "duplicate_command",
            "idempotency_key": idempotency_key,
        }
    return _stale(gen_id)


class WarehouseTools:
    """Holds the 7 MCP tools and their shared collaborators.

    The Policy Gate, gen checker, audit log and state store are injected so tools
    are independently testable with fakes (file-backed stores under ``tmp_path``).
    """

    def __init__(
        self,
        gen_checker: GenChecker | None = None,
        policy_gate: PolicyGate | None = None,
        audit: CommandAuditLog | None = None,
        state_store: StateStore | None = None,
        *,
        config: dict | None = None,
        nav2_forwarder: Nav2Forwarder | None = None,
        negotiation_starter: Callable[[dict[str, Any]], None] | None = None,
    ) -> None:
        """Wire collaborators; each defaults to its shared file-backed instance.

        ``nav2_forwarder`` forwards an ACCEPTED motion tool to the Nav2 Bridge
        REST API (doc12a:198-363). Injected by Mode A/B (llm_bridge) and, since
        #423, by Mode X-ER when config ``mode_x_er.dispatch.forward_to_nav2`` is
        true (x_er_bridge ``resolve_nav2_forwarder``, safe-OFF default). Left
        ``None`` (the default; Mode C / Open-RMF, and X-ER safe-OFF) the tools
        only validate + book-keep and actuate nothing —
        the pre-#86 behaviour every existing test relies on.

        ``negotiation_starter`` (Slice 2) is the ``/negotiation/start`` publisher
        (doc14:59,205): the bridge node injects a callback that builds the ROS message and
        publishes it. Called with the start-envelope dict (``negotiation_id, gen_id, starter,
        deadlock_or_escalation_id, context``) when ``start_negotiation`` accepts. Left ``None``
        (the default, and every non-ROS test) the tool keeps its pre-Slice-2 behaviour: validate
        + mint an id, publish nothing. It is advisory only (稟議制, doc14:38) — it never actuates,
        so it is NOT a motion forward and is unaffected by the R-26 ``_maybe_forward`` gate.
        """
        self._gen_checker = gen_checker or GenChecker()
        # A default PolicyGate resolves its freshness windows from config
        # (``policy_gate`` block, base defaults 0.5/2.0; doc12 §stale 判定). A
        # malformed value fails closed here = startup refusal. An explicitly
        # injected gate (tests) is used as-is.
        self._policy_gate = policy_gate or PolicyGate(
            state_store, freshness=freshness_from_config(config)
        )
        self._audit = audit or CommandAuditLog()
        self._state_store = state_store or FileStateStore()
        self._config = config or {}
        self._nav2_forwarder = nav2_forwarder
        self._negotiation_starter = negotiation_starter
        # In-memory escalation registry (TODO #escalation: replace with a shared
        # store once the escalation producer track lands; emergent dependency).
        self._escalations: dict[str, dict] = {}
        self._negotiation_seq = 0

    # ── wire entry: dispatch by tool name (server.py stdio boundary) ────────

    async def dispatch(self, name: str, arguments: dict) -> dict[str, Any]:
        """Resolve + invoke a tool from raw MCP args, ALWAYS returning a status dict.

        The stdio wire (``server.py``) routes every call through here so a missing
        ``gen_id``, an unknown/disallowed tool name, or malformed arguments become
        an audited ``{"status": ...}`` reject/error instead of an exception
        escaping onto the transport — which would skip the B-3 gen guard and the
        audit log (both live inside the tool bodies). ``idempotency_key``, when
        present in ``arguments``, flows through ``**args`` to the tool.
        """
        if name not in TOOL_NAMES:
            payload = {"status": "error", "reason": f"unknown_tool:{name}"}
            self._audit.record(name, "error", payload)
            return payload
        args = dict(arguments)
        gen_id = args.pop("gen_id", None)
        if gen_id is None:
            payload = {"status": "rejected", "reason": "missing_gen_id"}
            self._audit.record(name, "rejected", payload)
            return payload
        handler = getattr(self, name)
        try:
            result = await handler(gen_id, **args)
        except TypeError as exc:
            payload = {"status": "error", "reason": f"bad_arguments:{exc}"}
            self._audit.record(name, "error", payload)
            return payload
        await self._maybe_forward(name, result)
        return result

    async def _maybe_forward(self, name: str, result: dict[str, Any]) -> None:
        """Forward an ACCEPTED motion tool to the Nav2 Bridge (R-26 safety gate).

        Fires ONLY when a forwarder is wired (Mode A/B) AND the tool returned
        ``status == "ok"``: a stale-generation (B-3) / duplicate (C) / Policy-Gate
        rejection returns ``status != "ok"`` and so NEVER reaches a robot. Read-only
        / escalation / negotiation tools map to ``None`` and actuate nothing. The
        forwarder is fail-open, so a Nav2 Bridge outage is logged, not raised
        (doc12a:198-205 / doc15:198-205 / doc08a:164-173).
        """
        if self._nav2_forwarder is None or result.get("status") != "ok":
            return
        request = plan_nav2_request(name, result)
        if request is None:
            return
        # Fail-open at the seam: a forwarder fault (transport error, a missing
        # ``.[nav2]`` extra, a buggy injected forwarder) must NEVER propagate out of
        # dispatch — it would unwind through the executor / scheduler and silently
        # kill the commander cycle thread (llm_bridge only suppresses CancelledError)
        # while the node stays alive issuing no further commands. The Nav2Forwarder
        # ABC documents "never raises", but we enforce it HERE, where the type is
        # owned, so the guarantee holds for ANY forwarder.
        try:
            outcome = await self._nav2_forwarder.forward(request)
        except Exception as exc:  # fail-open: a forwarder fault must not kill the cycle
            log.warning("nav2 forward raised for %s -> POST %s: %s", name, request.path, exc)
            return
        log.info("nav2 forward %s -> POST %s %s: %s", name, request.path, request.body, outcome)

    # ── tool 1: dispatch_task ───────────────────────────────────────────────

    async def dispatch_task(
        self,
        gen_id: int,
        *,
        robot: str | None = None,
        pickup: str | None = None,
        dropoff: str | None = None,
        priority: str = "normal",
        via: str | None = None,
        action: str = "deliver",
        duration: float | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Assign / wait / yield a robot, validated by the Policy Gate (atomic).

        ``pickup`` is optional (action_map omits it). Location/same-location checks
        run only when the value is present. ``via`` / ``action`` / ``duration`` are
        Mode A/B traffic extensions (ignored by Mode C / Open-RMF).
        """
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("dispatch_task", "rejected", payload, robot=robot)
            return payload

        gate = await self._policy_gate.validate_and_register_dispatch(
            robot=robot, pickup=pickup, dropoff=dropoff, action=action
        )
        if not gate.accepted:
            payload = {"status": "rejected", "reason": gate.reason}
            self._audit.record("dispatch_task", "rejected", payload, robot=robot)
            return payload

        payload = {
            "status": "ok",
            "task_id": gate.task_id,
            "robot": robot,
            "action": action,
            "dropoff": dropoff,
            "via": via,
            "priority": priority,
            "duration": duration,
        }
        self._audit.record("dispatch_task", "executed", payload, robot=robot, gen_id=gen_id)
        return payload

    # ── tool 2: cancel_task ─────────────────────────────────────────────────

    async def cancel_task(
        self, gen_id: int, *, task_id: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Cancel a task; ``"current:{robot}"`` resolves via ``active_tasks`` (locked)."""
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("cancel_task", "rejected", payload)
            return payload

        resolved = task_id
        robot: str | None = None
        if task_id.startswith("current:"):
            robot = task_id.split(":", 1)[1]
            resolved = await self._policy_gate.resolve_and_clear_active(robot)
            if resolved is None:
                payload = {"status": "rejected", "reason": "no_active_task", "robot": robot}
                self._audit.record("cancel_task", "rejected", payload, robot=robot)
                return payload
        else:
            # Direct task_id (a documented cancel form, doc15/08a): still free the
            # destination so a cancelled delivery stops blocking duplicate_destination.
            # robot may be None if the gate never registered this id (lenient cancel).
            robot = await self._policy_gate.resolve_and_clear_by_task_id(task_id)

        payload = {"status": "ok", "task_id": resolved, "robot": robot}
        self._audit.record("cancel_task", "executed", payload, robot=robot, gen_id=gen_id)
        return payload

    # ── tool 3: get_fleet_status ────────────────────────────────────────────

    async def get_fleet_status(
        self, gen_id: int, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Return the latest fleet state snapshot (read-only)."""
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("get_fleet_status", "rejected", payload)
            return payload

        state = self._state_store.read() or {}
        # `or {}` (not get(default)): a present-but-null "robots" must not reach
        # len() / .get() and crash a read-only tool.
        robots = state.get("robots") or {}
        payload = {
            "status": "ok",
            "timestamp": state.get("timestamp"),
            "robots": robots,
        }
        self._audit.record("get_fleet_status", "executed", {"robots": len(robots)})
        return payload

    # ── tool 4: get_task_queue ──────────────────────────────────────────────

    async def get_task_queue(
        self, gen_id: int, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Return active tasks plus pending/recent stubs (read-only)."""
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("get_task_queue", "rejected", payload)
            return payload

        payload = {
            "status": "ok",
            "active": dict(self._policy_gate.active_tasks),
            # TODO(#nav2-bridge): pending/recent come from the task store once wired.
            "pending": [],
            "recent": [],
        }
        self._audit.record("get_task_queue", "executed", {"active": len(payload["active"])})
        return payload

    # ── tool 5: send_to_charging ────────────────────────────────────────────

    async def send_to_charging(
        self, gen_id: int, *, robot: str, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        """Send ``robot`` to the charging station via the Policy Gate.

        Charging uses the dedicated charging path, which (unlike a delivery)
        **does not re-apply the low/critical battery gate** — a low battery is the
        reason to charge. Validate + register are atomic.
        """
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("send_to_charging", "rejected", payload, robot=robot)
            return payload

        gate = await self._policy_gate.validate_and_register_charging(robot)
        if not gate.accepted:
            payload = {"status": "rejected", "reason": gate.reason, "robot": robot}
            self._audit.record("send_to_charging", "rejected", payload, robot=robot)
            return payload

        payload = {
            "status": "ok",
            "task_id": gate.task_id,
            "robot": robot,
            "dropoff": "charging_station",
        }
        self._audit.record("send_to_charging", "executed", payload, robot=robot, gen_id=gen_id)
        return payload

    # ── tool 6: escalation_response ─────────────────────────────────────────

    async def escalation_response(
        self,
        gen_id: int,
        *,
        escalation_id: str,
        action: str,
        new_robot: str | None = None,
        reason: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Respond to an escalation (reassign / cancel / retry); shape-validated.

        A second response to an already-resolved escalation is re-rejected
        (``already_resolved``, doc15:337-338) — escalation-level idempotency,
        distinct from the per-call gen/idempotency guard above.

        TODO(#escalation): the escalation registry is in-memory; a follow slice
        wires it to the shared escalation store/topic. Today an unknown id is
        rejected and the response is recorded but not acted on downstream.
        """
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("escalation_response", "rejected", payload)
            return payload

        if action not in ESCALATION_ACTIONS:
            payload = {"status": "rejected", "reason": "unknown_action", "action": action}
            self._audit.record("escalation_response", "rejected", payload)
            return payload
        if escalation_id not in self._escalations:
            payload = {"status": "rejected", "reason": "unknown_escalation_id"}
            self._audit.record("escalation_response", "rejected", payload)
            return payload
        # doc15:337-338: an escalation already resolved by a prior response is
        # re-rejected, not acted on twice — idempotency at the escalation level
        # (distinct from the per-call gen/B-3 + idempotency_key guard above). The
        # resolved marker lives on the in-memory registry entry (TODO #escalation:
        # a shared store replaces it). Action precedence is unchanged: a bogus
        # action above still wins; this only adds the missing resolved-reject branch.
        if self._escalations[escalation_id].get("resolved"):
            payload = {
                "status": "rejected",
                "reason": "already_resolved",
                "escalation_id": escalation_id,
            }
            self._audit.record("escalation_response", "rejected", payload)
            return payload

        self._escalations[escalation_id]["resolved"] = True
        payload = {
            "status": "ok",
            "escalation_id": escalation_id,
            "action": action,
            "new_robot": new_robot,
            "reason": reason,
        }
        self._audit.record("escalation_response", "executed", payload, robot=new_robot)
        return payload

    # ── tool 7: start_negotiation ───────────────────────────────────────────

    async def start_negotiation(
        self,
        gen_id: int,
        *,
        deadlock_or_escalation_id: str,
        starter: str,
        context: str = "",
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Kick off a character-LLM negotiation (doc14:59); mint an id and publish the start.

        Validates the generation (B-3) + starter, mints ``negotiation_id``, and — when a
        ``negotiation_starter`` is wired (Slice 2, the bridge node's ``/negotiation/start``
        publisher) — emits the start envelope so the ``character_llm`` node begins the bot1/bot2
        baton-pass (doc14:59-93). The resulting ``/negotiation/proposal`` is fed back into the
        next commander cycle's situation by the bridge (scheduler.set_negotiation_proposal,
        doc14:62-63). With no starter wired (non-ROS tests / Mode-less runs) it still returns the
        id but publishes nothing. Advisory only — no actuation (稟議制, doc14:38).
        """
        res = await self._gen_checker.check(gen_id, idempotency_key)
        if not res.ok:
            payload = _gen_reject(res, gen_id, idempotency_key)
            self._audit.record("start_negotiation", "rejected", payload)
            return payload

        if starter not in NEGOTIATION_STARTERS:
            payload = {"status": "rejected", "reason": "unknown_starter", "starter": starter}
            self._audit.record("start_negotiation", "rejected", payload)
            return payload

        self._negotiation_seq += 1
        negotiation_id = f"nego_{self._negotiation_seq:03d}"
        payload = {
            "status": "ok",
            "negotiation_id": negotiation_id,
            "deadlock_or_escalation_id": deadlock_or_escalation_id,
            "starter": starter,
            "context": context,
        }
        # Audit-then-act (the codebase convention, cf. dispatch->_maybe_forward): record the
        # accepted negotiation BEFORE the publish side effect so the executed row exists even if
        # the (fail-open) publish faults.
        self._audit.record("start_negotiation", "executed", payload)
        self._publish_negotiation_start(gen_id, payload)
        return payload

    def _publish_negotiation_start(self, gen_id: int, payload: dict[str, Any]) -> None:
        """Emit the ``/negotiation/start`` envelope via the injected publisher (doc14:59,205).

        Fail-open at the seam (mirrors ``_maybe_forward``): a publisher fault must NEVER unwind
        out of the tool — that would turn an advisory演出 trigger into a commander-cycle crash.
        Includes ``gen_id`` (stamped onto the eventual proposal, doc14:70,142). No-op when no
        starter is wired.
        """
        if self._negotiation_starter is None:
            return
        envelope = {
            "negotiation_id": payload["negotiation_id"],
            "gen_id": gen_id,
            "starter": payload["starter"],
            "deadlock_or_escalation_id": payload["deadlock_or_escalation_id"],
            "context": payload["context"],
        }
        try:
            self._negotiation_starter(envelope)
        except Exception:  # noqa: BLE001 - fail-open seam (doc14:38 advisory; never crash dispatch)
            log.exception(
                "negotiation_starter failed for %s (fail-open)", envelope["negotiation_id"]
            )
