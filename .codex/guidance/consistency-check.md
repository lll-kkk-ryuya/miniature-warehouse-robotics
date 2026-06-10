# Consistency Check

Source reference: `.claude/rules/consistency-check.md`.

Run `python3 scripts/check_consistency.py` after touching:

- `docs/**`
- `ws/src/warehouse_interfaces/**`
- `ws/src/warehouse_description/**`
- `config/**`

Treat ERROR as blocking. WARN requires review and should not be mass-fixed when
it belongs to another owner or needs design judgment.

For semantic drift that the checker cannot detect, use `$consistency-audit`.
