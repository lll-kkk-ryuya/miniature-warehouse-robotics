"""Console-script entry point: preflight the heavy deps, then run the gateway (#283).

The ``web_bridge`` executable resolves here rather than straight to
:func:`warehouse_web_bridge.web_bridge_node.main` because that module imports ``rclpy`` /
``uvicorn`` **at module load** — so in an under-provisioned image the process dies during
import, before any ``main()`` body could explain why. Deferring that import into a guarded
call is the only place the explanation can be produced (see :mod:`warehouse_web_bridge.preflight`).

The guard spans the CALL as well as the import, because the two remaining heavy deps arrive
later: ``fastapi`` is imported inside ``create_app`` and ``websockets`` is loaded by uvicorn at
serve time. A runtime ``ImportError`` is converted **only** when
:func:`~warehouse_web_bridge.preflight.is_provisioning_failure` recognises the module, so an
unrelated import bug keeps its traceback instead of being mislabelled a provisioning problem.

Behaviour is otherwise unchanged: the executable name stays ``web_bridge`` and, once the deps
are present, this is a single extra function call before the identical gateway startup.
Exit code 1 on a missing dependency (setuptools' console-script wrapper does
``sys.exit(main())``), so a launch/systemd supervisor sees a real failure rather than a
traceback-shaped one.
"""

from __future__ import annotations

import sys

from warehouse_web_bridge.preflight import is_provisioning_failure, missing_dependency_hint


def main() -> int:
    """Run the gateway; on a missing heavy dep print an actionable hint and exit non-zero."""
    try:
        from warehouse_web_bridge.web_bridge_node import main as run_gateway
    except ImportError as exc:
        print(missing_dependency_hint(exc), file=sys.stderr)
        return 1
    try:
        run_gateway()
    except ImportError as exc:
        # Not every heavy dep is reached by the import above: ``fastapi`` is imported INSIDE
        # ``create_app`` (app.py) and ``websockets`` is pulled in by uvicorn when the server
        # starts, so their absence surfaces here — the same #283 symptom, one call later.
        # Convert only what the hint table can actually explain; anything else keeps its
        # traceback, so a genuine runtime bug is never disguised as a provisioning problem.
        if not is_provisioning_failure(exc):
            raise
        print(missing_dependency_hint(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
