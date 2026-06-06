# Safety Guidance

Source reference: `.claude/rules/safety.md`.

- Do not commit credentials, API keys, WiFi passwords, or cloud GPU secrets.
- Do not read `.env`, `config/**/.env`, or `secrets/**` unless the user gives an
  explicit, scoped request that requires those exact files.
- Use `.env.example` and placeholder values for documentation, Issues, PRs, and
  generated examples.
- Enforce robot speed limits in code; miniature scale max is 0.3 m/s.
- Test emergency stop logic before physical robot demos.
- Do not store cloud GPU credentials in Isaac Sim configuration.
