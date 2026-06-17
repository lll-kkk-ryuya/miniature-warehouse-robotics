# Miniature Warehouse Robotics - Codex Instructions

This file is the Codex equivalent of `.claude/CLAUDE.md`. The `.claude/`
directory is the Claude Code source and must not be edited by Codex migration
work unless the user explicitly asks for Claude Code changes.

## Project Overview

Miniature warehouse robotics demo on a 1.8m x 0.9m diorama with two autonomous
robots. LLMs act as commanders and issue real-time instructions for a YouTube
comparison demo.

## Tech Stack

- ROS 2 Jazzy, Nav2, SLAM Toolbox, AMCL
- micro-ROS on ESP32 Yahboom MicroROS Car x 2
- Jetson Orin Nano Super for Nav2 and LLM Bridge
- Python LLM Bridge for Claude, ChatGPT, Gemini, and Grok APIs
- RPLiDAR A1 for fixed external tracking correction
- Gazebo Harmonic in Docker on Mac M4
- Isaac Sim 5.1 on RunPod A10G
- Warehouse Orchestrator for diagnostics and KPIs

## Development Environment

- MacBook Pro M4 16GB on macOS Sequoia for development.
- Docker image `tiryoh/ros2-desktop-vnc:jazzy` for ARM64 ROS 2 development.
- Jetson Orin Nano Super on Ubuntu 24.04 + ROS 2 Jazzy for runtime.
- WiFi is tethering or router-based for micro-ROS and LLM API traffic.

## Communication

- Write project documentation in Japanese.
- Use English for code comments, identifiers, and commit messages.
- Report file references as repo-relative or clickable absolute paths with line
  numbers when possible.

## Code Conventions

- Python follows PEP 8 with type hints.
- ROS 2 packages follow `ament_python` or `ament_cmake`.
- C++ ROS 2 nodes follow Google C++ Style Guide.
- YAML config and parameter files use 2-space indentation.
- Use Python launch files (`.launch.py`), not XML.

## Core Rules

- Treat `docs/` as the source of truth. Before planning, implementation, review,
  Issue creation, or PR creation, read `docs/README.md`, `docs/STATUS.md`, and
  the relevant design documents.
- Do not invent topics, schemas, thresholds, paths, or contracts that are absent
  from docs. If docs are missing, stop and update docs first.
- When citing docs, verify the file directly and cite `path:line`; do not rely on
  memory or summaries.
- For package work under `ws/src/warehouse_*`, read the package `CLAUDE.md`.
  `.codex/config.toml` also configures `CLAUDE.md` as a fallback project-doc name
  so Codex can load package-level guidance without editing those files.
- Keep implementations dependent only on frozen contracts such as
  `warehouse_interfaces` and shared descriptions. Do not import internals from
  another track package.
- Record public interfaces while implementing: produce/consume topics, files,
  schemas, and assumptions belong in the relevant package guidance or docs.
- Run `python3 scripts/check_consistency.py` after touching docs, frozen
  contracts, shared descriptions, or config. Treat ERROR as a blocking drift.
- Secrets, API keys, WiFi passwords, cloud GPU credentials, and `.env` values
  must not be committed. Use `.env.example` only for placeholders.
- Do not read `.env`, `config/**/.env`, or `secrets/**` unless the user gives an
  explicit, scoped request that requires those exact files.
- Main worktree is integration-only. Development should happen in a feature,
  docs, fix, chore, or hw branch/worktree and land through PR.
- Do not create or merge Issue/PR content without docs links and the required
  worktree tag. Simple one-line Issues/PRs are prohibited.

## Testing

Python tests run on the host **without ROS 2 or colcon** — `conftest.py` puts
each `ws/src/<pkg>` on `sys.path`, and CI runs them on Python 3.12
(`.github/workflows/ci.yml`, job `python-quality`). But the host default
`python3` is **3.7 with no `pytest`**, so bare `pytest` / `python3 -m pytest`
fail with exit 127. Use the project venv (Python 3.12) instead:

- Run tests: `.venv/bin/python -m pytest`
- Lint/format like CI: `.venv/bin/ruff check .` / `.venv/bin/ruff format --check .`
- Create the venv once if it is missing:
  `python3.12 -m venv .venv && .venv/bin/python -m pip install -U ruff pytest pytest-cov "pydantic>=2" pyyaml`
- `.venv/` is gitignored — never commit it.
- Tests that import `launch_ros` (Nav2 launch-introspection) and
  `user-docker-gated` e2e harnesses **SKIP** without ROS/Gazebo; skips are
  expected, not failures.
- C++ firmware Layer-0 safety runs via a host shim:
  `bash firmware/test/run_host_test.sh` (no PlatformIO/ESP32 needed).

## Codex-Specific Mapping

- Detailed migrated guidance lives under `.codex/guidance/`.
- Custom Codex agents live under `.codex/agents/`.
- Codex skills live under `.agents/skills/`.
- Codex hooks are configured in `.codex/hooks.json` and implemented under
  `.codex/hooks/`. Project-local hooks require trust review in Codex.
- Codex command approval rules live under `.codex/rules/`.

## Important Paths

- `docs/README.md` - documentation map
- `docs/STATUS.md` - project status and dependency state
- `docs/shared/` - mode-independent overview, budget, hardware, and shared docs
- `docs/architecture/03-software-architecture.md`
- `docs/architecture/06-implementation-phases.md`
- `docs/architecture/08-llm-bridge-common.md`
- `docs/architecture/12-infrastructure-common.md`
- `docs/architecture/16-repository-and-conventions.md`
- `docs/architecture/17-development-workflow.md`
- `docs/architecture/19-environments-and-config.md`
- `deploy/` - deployment assets and runbooks
- `docs/mode-a/` - Mode A/B design
- `docs/mode-c/` - Mode C design
