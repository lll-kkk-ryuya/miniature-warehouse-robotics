# deploy/dev/xer6 — G5 無償 offline-replay 実行 artifact

G5（#342・sim human gate）を **稼働 `x_er_bridge` node・課金ゼロ**で回すための commit 済み
artifact 一式。**運転手順書は [docs/dev/08-xer6-live-sim-x-lite-runbook.md](../../../docs/dev/08-xer6-live-sim-x-lite-runbook.md) 追補 2**
（config key の凍結は [docs/mode-x-er/08-x-er-bridge-node-spec.md](../../../docs/mode-x-er/08-x-er-bridge-node-spec.md) §3）。

| ファイル | 役割 |
|---|---|
| `er_request.red_blue.json` | `ErTaskRequest` fixture（`mode_x_er.request_fixture`） |
| `er_offline_payload.direct.json` | 録画済み ER 応答 envelope（`mode_x_er.er_offline_payload`） |
| `run_manifest.yaml` | plugin-less `run_manifest.v1`（zero-plugin baseline） |
| `site_profiles/customer_a/site_01/` | `APPROVED.yaml` 付き site profile bundle（**dev-sim 専用承認**） |
| `warehouse.dev-overlay.example.yaml` | `config/dev/warehouse.yaml` へ手動 merge する overlay 例 |

artifact の整合は CI unit `tests/unit/test_xer6_g5_replay_artifacts.py` が固定する
（腐ればテストが赤くなる）。live/paid ER leg は従来どおり dev/08 §7 の optional・human-gate。
