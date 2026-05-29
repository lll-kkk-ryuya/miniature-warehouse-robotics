"""warehouse_interfaces: the frozen contract shared by all tracks.

Provides the canonical location set, runtime path resolution, the
``StateStore`` / ``GenStore`` interfaces, and the pydantic schemas for the
JSON exchanged over ``std_msgs/String`` topics (doc16 §3).

This package is ament_python and depends only on stdlib + pydantic. Other
tracks import from here and MUST NOT change the contract without the
contract-change protocol (.claude/rules/parallel-workflow.md §4).
"""
