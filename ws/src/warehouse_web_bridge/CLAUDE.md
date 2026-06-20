# warehouse_web_bridge — Web Observability gateway（observe-only・doc22）

- **担当トラック / ブランチ**: `track:web` / `feat/web`
- **Phase**: 3（observe-only console。設計正本 doc22 §13 スライス）
- **編集境界**: このパッケージ配下のみ（＋root `tests/unit/test_web_*.py`）。他パッケージ・共有契約は触らない（契約変更は parallel-workflow §4）。`config/warehouse.base.yaml` の `web_bridge` ブロックは bringup/skeleton 所有＝予告経由（§18#5・S2 PR で追加予定）。
- **消費する契約 (consume)**: doc03 既存トピックの **JSON 文字列のみ**（`std_msgs/String`）。`/state_cache/snapshot`・`/llm/command`・`/llm/reasoning`(生text)・`/character/speech`・`/negotiation/{start,turn,proposal,abort}`・`/emergency/event`（doc03:98-108 / doc22:107-117）。**凍結 pydantic スキーマ / 会話 decoder は import しない**（JSON レベル処理で疎結合維持・doc22:154 / §18#4 は S1/S2 で non-issue）。
- **生産する契約 / トピック (produce)**: **ROS トピックは publish しない**（purely consumer）。提供するのは **ObsEvent 封筒**（WS/REST 上の wire 形・doc22 §5:136-160。ROS 契約ではないので doc03 カタログに producer 追加しない・doc22:119）と、`events-<run_id>.jsonl`（per-run JSON Lines・additive sink・doc22 §9）。
- **依存**: `warehouse_interfaces` のみ（runtime。S1 純コアは stdlib のみで `warehouse_interfaces` も未 import）。S2 で `rclpy` + `fastapi`/`uvicorn`/`websockets`（lazy import・nav2_bridge 先例）。他トラック内部を import しない。
- **テスト**: 偽 publisher 入力 → ObsEvent / events.jsonl / replay を **host pytest**（`tests/unit/test_web_obs_event.py`・`test_web_event_log.py`）。安全は **R-26 no-actuation unit**（`tests/unit/test_web_bridge_noactuation.py`・AST source-scan）必須。すべて ROS / network / SDK 非依存（doc16 §11）。
- **設計ドキュメント**: [docs/architecture/22-web-observability.md](../../../docs/architecture/22-web-observability.md)（正本）/ 共存パターン [doc12a:200-234](../../../docs/mode-a/12a-integration-mode-a.md) / トピック契約 [doc03:98-108](../../../docs/architecture/03-software-architecture.md)。

## スライス進捗（doc22 §13）

- **S1（本パッケージ初期化・rclpy-free offline core）**: `kind_map` / `obs_event`（ObsEvent 正規化・malformed never-raise・seq 唯一権威）/ `event_log`（append / rotation / retention / `since_seq` replay）/ `ingest`（単一 ingest 点・seq 採番・trace_deriver seam）。R-26 unit 同梱。**trace_id は常に null**（fail-open・Langfuse SDK 非依存・doc22:152,:194。live 導出は S2 以降）。
- **S2（予定）**: `web_bridge_node`（rclpy matching-QoS subscribe）+ `coalescer`（snapshot 10→2Hz・state last-write-wins）+ `app`（FastAPI: `/ws` `/events` `/runs` `/config` `/health` + StaticFiles mount）+ config 読込（fail-open default 127.0.0.1/8646/2Hz）+ bind 127.0.0.1。entry_point `web_bridge = ...:main`。
- **未決の暫定値**: `recordings_dir` は config 注入（具体 SSD path は #187 段階2 後・doc22:379/S6）。rotation 予算（`max_bytes`/`max_runs`）も注入（doc22:221）。`run_id` は S2.5 `/run/header` land まで synthetic fallback（doc22:303）。
