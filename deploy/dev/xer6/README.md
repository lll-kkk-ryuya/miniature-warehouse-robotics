# deploy/dev/xer6 — G5 無償 offline-replay 実行 artifact

G5（#342・sim human gate）を **稼働 `x_er_bridge` node・課金ゼロ**で回すための commit 済み
artifact 一式。**運転手順書は [docs/dev/08-xer6-live-sim-x-lite-runbook.md](../../../docs/dev/08-xer6-live-sim-x-lite-runbook.md) 追補 2**
（config key の凍結は [docs/mode-x-er/08-x-er-bridge-node-spec.md](../../../docs/mode-x-er/08-x-er-bridge-node-spec.md) §3）。

| ファイル | 役割 |
|---|---|
| `er_request.red_blue.json` | v1: `ErTaskRequest` fixture（`mode_x_er.request_fixture`） |
| `er_offline_payload.direct.json` | v1: 録画済み ER 応答 envelope（`mode_x_er.er_offline_payload`） |
| `er_request.choreography_v2.json` | **v2**（本番デモ振り付け・dev/08 追補 3）: t1–t5 指示の `ErTaskRequest` fixture |
| `er_offline_payload.choreography_v2.json` | **v2**: t1–t5（after 付き完了依存 DAG）の録画済み ER envelope |
| `run_manifest.yaml` | plugin-less `run_manifest.v1`（zero-plugin baseline・**v1/v2 共用**） |
| `site_profiles/customer_a/site_01/` | `APPROVED.yaml` 付き site profile bundle（**dev-sim 専用承認**・v1/v2 共用） |
| `warehouse.dev-overlay.example.yaml` | `config/dev/warehouse.yaml` へ手動 merge する overlay 例（v1↔v2 切替は `request_fixture` / `er_offline_payload` の 2 key 差し替えのみ・ファイル内コメント参照） |

v1（赤箱/青箱 2 タスク）は機構検証ベースライン、v2（t1–t5）は #342 G5 の本番デモ振り付け
（[docs/dev/08 追補 3](../../../docs/dev/08-xer6-live-sim-x-lite-runbook.md)・依存語彙の規範は
[docs/mode-x-er/02 の 2026-07-11 裁定節](../../../docs/mode-x-er/02-l3-planning-core.md)）。
artifact の整合は CI unit が固定する（腐ればテストが赤くなる）: v1 =
`tests/unit/test_xer6_g5_replay_artifacts.py`、v2 = `tests/unit/test_xer6_g5_choreography_v2.py`。
live/paid ER leg は従来どおり dev/08 §7 の optional・human-gate。
