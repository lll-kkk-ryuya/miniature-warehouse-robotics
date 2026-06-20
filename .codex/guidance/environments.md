# Environment Guidance

Source reference: `.claude/rules/environments.md`.

- Select runtime environment with `WAREHOUSE_ENV` (`dev`, `stg`, `prod`).
- Default to `dev` when unset.
- Read URLs, paths, modes, and sim/hardware flags from config.
- Do not hardcode environment names, endpoints, secrets, or keys in code.
- Use config layering:
  `config/warehouse.base.yaml` -> `config/$WAREHOUSE_ENV/warehouse.yaml` -> env vars.
- Commit only `.env.example` placeholders. Never commit real `.env` files.
- Prod/hardware operation requires Emergency Guardian and 0.3 m/s speed-limit
  tests to pass.

## Dev Live Hermes / LLM Bridge

- For Gazebo/RViz dev live runs with Hermes + LLM Bridge, use
  `deploy/dev/run-mode-a-live.sh` instead of manual `ros2 launch`.
- Fully one-command dev mode is `deploy/dev/run-mode-a-live.sh --start-hermes`.
- Use `deploy/dev/check-hermes-live.sh` for diagnostics-only checks before
  launching ROS. It verifies Hermes `/health`, authenticated `/v1/models`, and
  optional container reachability.
- From Docker-on-Mac sim containers, use `http://host.docker.internal:8642` for
  host Hermes. Do not use `localhost` from inside the container.
- Pass only Bridge-side auth (`API_SERVER_KEY` / `HERMES_API_KEY`) and
  observability keys into ROS/sim containers. Provider keys stay in Hermes'
  `~/.hermes/.env`.
- Restart the full stack after `.env` changes. A running `llm_bridge` does not
  pick up changed environment variables.
- Do not read `config/<env>/.env` or `~/.hermes/.env` unless the user gives an
  explicit scoped request naming the path and purpose. Never print secret values.
- If a feature worktree has no `config/dev/.env`, use
  `--env-file /path/to/config/dev/.env` or `MWR_HERMES_ENV_FILE`.
- If agent guardrails block `.env` reads, have the user export
  `API_SERVER_KEY` / `HERMES_API_KEY` in their shell and run with
  `MWR_HERMES_ENV_FILE=/nonexistent`.
