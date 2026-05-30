"""The 7 Warehouse MCP tools — transport-agnostic core logic (doc15 §ツール定義).

Each tool is a plain ``async def`` on :class:`WarehouseTools`, fully unit-testable
with NO MCP library and NO network. ``server.py`` wraps these for the stdio wire.

Invariants enforced on EVERY tool:

* First line is ``await self._gen_checker.check(gen_id)`` — a stale (B-3) call is
  rejected before any side effect (doc15 §2).
* Keyword-only args (after ``gen_id``) match ``warehouse_llm_bridge.action_map``
  EXACTLY, so ``await getattr(tools, tc.tool)(**tc.args)`` works verbatim.
* Returns a dict with a ``status`` key: ``"ok" | "rejected" | "error"``.
* Every outcome is written to the audit log with ``result`` in
  ``{"executed", "rejected", "error"}``.

Divergence from doc15: ``dispatch_task`` takes ``pickup`` as optional
(``pickup=None``) because ``action_map`` never sends it (it carries only
``dropoff``); pickup-dependent checks run only when ``pickup is not None``. See
CLAUDE.md.

Pure Python — imports only ``warehouse_interfaces`` + sibling modules. No rclpy.
"""

from typing import Any

from warehouse_interfaces.stores import FileStateStore, StateStore

from warehouse_mcp_server.audit import CommandAuditLog
from warehouse_mcp_server.gen_check import GenChecker
from warehouse_mcp_server.policy_gate import PolicyGate

# Valid character-LLM negotiation starters (doc14 / doc15 ツール7).
NEGOTIATION_STARTERS = ("bot1", "bot2")
# Valid escalation_response actions (doc15 ツール6 / validate_escalation).
ESCALATION_ACTIONS = ("reassign", "cancel", "retry")


def _stale(gen_id: int) -> dict[str, Any]:
    """Build the canonical stale-generation rejection payload (doc15 §2)."""
    return {"status": "rejected", "reason": "stale_generation", "received_gen": gen_id}


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
    ) -> None:
        """Wire collaborators; each defaults to its shared file-backed instance."""
        self._gen_checker = gen_checker or GenChecker()
        self._policy_gate = policy_gate or PolicyGate(state_store)
        self._audit = audit or CommandAuditLog()
        self._state_store = state_store or FileStateStore()
        self._config = config or {}
        # In-memory escalation registry (TODO #escalation: replace with a shared
        # store once the escalation producer track lands; emergent dependency).
        self._escalations: dict[str, dict] = {}
        self._negotiation_seq = 0

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
    ) -> dict[str, Any]:
        """Assign / wait / yield a robot, validated by the Policy Gate (atomic).

        ``pickup`` is optional (action_map omits it). Location/same-location checks
        run only when the value is present. ``via`` / ``action`` / ``duration`` are
        Mode A/B traffic extensions (ignored by Mode C / Open-RMF).
        """
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("dispatch_task", "rejected", _stale(gen_id), robot=robot)
            return _stale(gen_id)

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
        self._audit.record("dispatch_task", "executed", payload, robot=robot)
        return payload

    # ── tool 2: cancel_task ─────────────────────────────────────────────────

    async def cancel_task(self, gen_id: int, *, task_id: str) -> dict[str, Any]:
        """Cancel a task; ``"current:{robot}"`` resolves via ``active_tasks`` (locked)."""
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("cancel_task", "rejected", _stale(gen_id))
            return _stale(gen_id)

        resolved = task_id
        robot: str | None = None
        if task_id.startswith("current:"):
            robot = task_id.split(":", 1)[1]
            resolved = await self._policy_gate.resolve_and_clear_active(robot)
            if resolved is None:
                payload = {"status": "rejected", "reason": "no_active_task", "robot": robot}
                self._audit.record("cancel_task", "rejected", payload, robot=robot)
                return payload

        payload = {"status": "ok", "task_id": resolved, "robot": robot}
        self._audit.record("cancel_task", "executed", payload, robot=robot)
        return payload

    # ── tool 3: get_fleet_status ────────────────────────────────────────────

    async def get_fleet_status(self, gen_id: int) -> dict[str, Any]:
        """Return the latest fleet state snapshot (read-only)."""
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("get_fleet_status", "rejected", _stale(gen_id))
            return _stale(gen_id)

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

    async def get_task_queue(self, gen_id: int) -> dict[str, Any]:
        """Return active tasks plus pending/recent stubs (read-only)."""
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("get_task_queue", "rejected", _stale(gen_id))
            return _stale(gen_id)

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

    async def send_to_charging(self, gen_id: int, *, robot: str) -> dict[str, Any]:
        """Send ``robot`` to the charging station via the Policy Gate.

        Charging uses the dedicated charging path, which (unlike a delivery)
        **does not re-apply the low/critical battery gate** — a low battery is the
        reason to charge. Validate + register are atomic.
        """
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("send_to_charging", "rejected", _stale(gen_id), robot=robot)
            return _stale(gen_id)

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
        self._audit.record("send_to_charging", "executed", payload, robot=robot)
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
    ) -> dict[str, Any]:
        """Respond to an escalation (reassign / cancel / retry); shape-validated.

        TODO(#escalation): the escalation registry is in-memory; a follow slice
        wires it to the shared escalation store/topic. Today an unknown id is
        rejected and the response is recorded but not acted on downstream.
        """
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("escalation_response", "rejected", _stale(gen_id))
            return _stale(gen_id)

        if action not in ESCALATION_ACTIONS:
            payload = {"status": "rejected", "reason": "unknown_action", "action": action}
            self._audit.record("escalation_response", "rejected", payload)
            return payload
        if escalation_id not in self._escalations:
            payload = {"status": "rejected", "reason": "unknown_escalation_id"}
            self._audit.record("escalation_response", "rejected", payload)
            return payload

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
    ) -> dict[str, Any]:
        """Kick off a character-LLM negotiation (doc14); returns a stub id.

        TODO(#negotiation): a follow slice publishes ``/negotiation/start`` and
        feeds the resulting ``/negotiation/proposal`` back into the next cycle's
        situation JSON. Today only the starter is validated and an id is minted.
        """
        res = await self._gen_checker.check(gen_id)
        if not res.ok:
            self._audit.record("start_negotiation", "rejected", _stale(gen_id))
            return _stale(gen_id)

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
        self._audit.record("start_negotiation", "executed", payload)
        return payload
