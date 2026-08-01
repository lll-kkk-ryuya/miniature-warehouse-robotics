"""Console-script entry point: preflight the heavy deps, then run the gateway (#283).

The ``web_bridge`` executable resolves here rather than straight to
:func:`warehouse_web_bridge.web_bridge_node.main` because that module imports ``rclpy`` /
``uvicorn`` **at module load** — so in an under-provisioned image the process dies during
import, before any ``main()`` body could explain why. Deferring that import into a guarded
call is the only place the explanation can be produced (see :mod:`warehouse_web_bridge.preflight`).

Behaviour is otherwise unchanged: the executable name stays ``web_bridge`` and, once the deps
are present, this is a single extra function call before the identical gateway startup.
Exit code 1 on a missing dependency (setuptools' console-script wrapper does
``sys.exit(main())``), so a launch/systemd supervisor sees a real failure rather than a
traceback-shaped one.
"""

from __future__ import annotations

import sys

from warehouse_web_bridge.preflight import missing_dependency_hint


def main() -> int:
    """Run the gateway; on a missing heavy dep print an actionable hint and exit non-zero."""
    try:
        from warehouse_web_bridge.web_bridge_node import main as run_gateway
    except ImportError as exc:
        print(missing_dependency_hint(exc), file=sys.stderr)
        return 1
    run_gateway()
    return 0


if __name__ == "__main__":
    sys.exit(main())
