"""Pytest path setup for ament_python packages under ws/src.

Each ament_python package nests its importable module as
``ws/src/<pkg>/<pkg>/`` (e.g. ws/src/warehouse_interfaces/warehouse_interfaces/).
The pyproject ``pythonpath`` cannot express this per-package nesting, so add
each package dir to ``sys.path`` here, letting tests ``import <pkg>`` without a
colcon build (doc16 §11: packages must be testable without ROS 2).
"""

import sys
from pathlib import Path

_SRC = Path(__file__).parent / "ws" / "src"
if _SRC.is_dir():
    for _pkg in sorted(_SRC.iterdir()):
        if (_pkg / _pkg.name / "__init__.py").exists():
            sys.path.insert(0, str(_pkg))
