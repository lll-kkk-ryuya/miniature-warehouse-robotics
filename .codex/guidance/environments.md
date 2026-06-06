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
