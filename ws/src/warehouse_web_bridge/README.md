# warehouse_web_bridge

Web Observability gateway（**observe-only**）。Mode A/B のキャラLLM会話・稟議・司令官判断・ロボット状態・緊急を、既存 ROS トピック（`std_msgs/String` JSON）から購読し、ブラウザへ **WebSocket / REST** で fan-out する。ブラウザ→ロボットの操作経路は持たない（R-26・doc22 §12.3）。

設計正本: [docs/architecture/22-web-observability.md](../../../docs/architecture/22-web-observability.md)。

## 構成（doc22 §2）

```
既存 producer（購読者ゼロ）──DDS──▶ web_bridge（rclpy subscriber + FastAPI）──WS/REST──▶ ブラウザ SPA
  /character/speech 等               ① ObsEvent 正規化 + seq 採番              （web/console・Next.js
  /llm/{reasoning,command}           ② events-<run_id>.jsonl append              static export・S3）
  /state_cache/snapshot(10Hz)        ③ snapshot coalesce(2Hz・S2)
  /emergency/event                   ④ WS fan-out / since_seq replay（S2）
```

## モジュール（S1 = rclpy/FastAPI 非依存の offline core）

| モジュール | 役割 |
|---|---|
| `kind_map` | `source_topic → ObsEvent kind` 静的マップ（doc22:107-117） |
| `obs_event` | ObsEvent 封筒正規化（malformed never-raise・gen_id dict 抽出・凍結 schema 非 import） |
| `event_log` | per-run JSON Lines（append / size rotation / N-runs retention / `since_seq` replay） |
| `ingest` | 単一 ingest 点（`seq` 単調採番＝順序の唯一権威・`trace_deriver` seam） |

S2 で `web_bridge_node`（rclpy）/ `coalescer` / `app`（FastAPI）を追加し、entry_point `web_bridge` を公開する。

## テスト（ROS 不要・host）

```bash
python3 -m pytest tests/unit/test_web_obs_event.py tests/unit/test_web_event_log.py \
                  tests/unit/test_web_bridge_noactuation.py -q
```

`test_web_bridge_noactuation.py` は **R-26 observe-only 契約**（publisher / service client / action client / actuation sink ゼロ）を AST source-scan で固定する（doc16 §11・先例 `test_modec_noactuation.py`）。
