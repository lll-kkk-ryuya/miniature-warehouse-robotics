# tests/e2e — slice2/3 統合ハーネス（#156 capstone）

`#156`「AI 司令官が Gazebo 2台を動かす」の **統合（integration）層**。`tests/unit/**`
（各 seam を fake で検証）に対し、ここは **配線（topology）と本番に近い経路** を検証する。
設計正本: `docs/architecture/06-implementation-phases.md:107-110`（Phase 0.5 完了条件）/
`docs/architecture/08-llm-bridge-common.md:121-169`（司令官サイクル・in-process dispatch）/
`docs/mode-a/08a-llm-bridge-mode-a.md:158-173,321-359,387`（action→Nav2 マッピング・デッドロック
検出と**解消シーケンス**＝yield/wait コマンドの正本・retreat LOCATIONS）/
`docs/mode-a/11a-traffic-mode-a.md:153`（検出は LLM の仕事）・`:431-470`（§9＝**別機構**の Mode-B
aisle-lock デモ＝live ≥0.15m 幾何の正本のみ）。

## 実行

```bash
# 自動テスト層（host で動く・ROS/network/Gazebo 不要）
python3.12 -m pytest tests/e2e/ -v            # = 統合配線の回帰
python3.12 -m pytest -m e2e                    # e2e だけ選択
python3.12 -m pytest -m "not e2e"              # e2e を除外
```

`e2e` マーカーは `tests/e2e/conftest.py` の `pytest_configure` で**ローカル登録**
（共有 `pyproject.toml`＝skeleton 所有を触らない。parallel-workflow.md §7.1）。

## このハーネスが証明すること（＝ここにしか無い価値）

`test_slice2_yield_forward.py`（4 tests）。**司令官サイクルを `llm_bridge.py:110-143`
と同一に配線**し、fake は**2つの真の外部境界だけ**＝ LLM の脳（Hermes Gateway）と
Nav2 の HTTP transport。両端は**本番コード**が走る:

| test | 証明すること |
|---|---|
| `test_headon_yield_forwards_both_motions` | **slice2 headline**: 実 head-on `state.json` → 実 `SituationBuilder` → 実 Hermes parser → action_map → 実 `WarehouseTools.dispatch` → forward。**08a:342-347 の正本デッドロック解消**（**bot2=yield→`/api/v1/navigate` retreat_B / bot1=wait→`/api/v1/wait` 5s**）の2 POST を doc 指定の順で**正確に**発火（配線の end-to-end）。 |
| `test_real_situation_builder_enriches_headon` | 実 `SituationBuilder`（Mode A）が head-on 生 snapshot を `obstacle_ahead=True` + CTRV `predicted_position_3s` + 全 traffic フィールドに enrich（commander が見る situation が**実物**＝stub でない）。 |
| `test_valid_json_but_invalid_command_is_ignored_no_forward` | 実 parser が valid-JSON-but-invalid-Command を dict 化 → scheduler の `Command.model_validate` が reject → cycle 無視・**0 forward**・loop 生存（parser↔scheduler を実接続）。 |
| `test_nonjson_reply_surfaces_scheduler_robustness_gap` | **統合 FINDING（→ L4 #181/#4）**: 非JSON応答で `decide()` の `ValueError` が `scheduler.run_cycle` を**伝播**（下記§FINDING）。現挙動を pin する tripwire。 |

## ここでは証明しない（責務分界・隠さない）

- **R-26 forward 抑止マトリクス**（accept→1 POST / stale・duplicate・Policy-reject・
  read-only→0 POST / fail-open）は **既に unit 層で凍結済**:
  `tests/unit/test_nav2_forward.py` ＋ `tests/unit/test_bridge_scheduler.py`
  （実 `WarehouseTools` で B-3 / C end-to-end）。**本ハーネスは重複させない**。
- **最接近 ≥0.15m の幾何**（`11a:446`）は **Gazebo 物理計測**＝下記 **slice3 live runbook**
  でのみ証明（host harness は物理を持たない＝WIRING のみ）。
- **§9 の実トポロジ**は再現しない: §9.2/§9.5（`11a:435,453,455`）の実デモは**座標ゴール**
  （x≈0.45,y≈0.12）＋ `route_A`/`route_B`＝**Mode-B aisle ロックキー**（route_planner が注入・
  `KNOWN_LOCATIONS` に**無い**＝navigate 宛先ではない）を使う（南側 named 地点は #144 で到達不能）。
  本 harness は **Mode-A `yield` コマンドの forward 配線**を、契約有効な `retreat_A/B`+`shelf_1`
  の**代理キー**で pin するだけ（座標ゴール・route ロックは slice3 live が扱う）。
- **デッドロック検出**は LLM 側推論（`11a:153`）。ここでは fake commander が代行。
  **実 Mode-A プロンプトは未配線（#181）**＝live で本物の yield 判断を出すのは slice3。

## slice3 live demo runbook（実 Hermes / RViz / noVNC 録画）

> **= 3段リリース第1段の素材**（sim 録画版が最初の公開/営業送付可成果物・round 戦略 2026-06-06）。
> **着手の前提**: ① **#181 land**（task 注入 + Mode-A system prompt＝L4 所有・slice2 blocker）
> ② **L6 のサイクル長確定**（API p95>2.5s なら 3秒→4-5秒・デモ尺の作り直しを断つ）
> ③ 下記 §FINDING（非JSON応答 robustness）の L4 対応。①②③が揃うまで本 runbook は**手順の据え置き**。

tiryoh コンテナ（host py3.12 では ROS/launch/Gazebo 不可＝`reference_local_gate_execution`）で:

```bash
# 0) 外部 daemon（launch では合成しない・bringup.launch.py:38-47）
#    Hermes Gateway :8642（dev キー疎通済＝memory project_api_keys_dev_setup）
#    Nav2 Bridge   :8645（REST→BasicNavigator, #86）を別途起動。

# 1) slice1 health（upstream 不要・今すぐ可能。DoD step1）
ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true
#   → Nav2 lifecycle 全 bot active / state_cache が state.json を 100ms 書出 /
#     guardian 50ms reflex / llm:=false でも起動（Hermes 無し fallback）を確認。

# 2) slice2/3 full stack（#181 land 後）
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true
#   sim+nav2+state+safety+nav2_bridge(:211-214 allowlist)+llm を合成（#162）。
#   2台に対向タスク（§9.2 北 staging ↔ 通路A 南端の座標ゴール・route_A はロックキー）を投入し、
#   LLM が 08a:337-359 の yield+wait → MCP → nav2_bridge → Nav2 で最接近 ≥0.15m を計測（11a:446）。

# 3) 録画: noVNC 画面（rviz:=true で minicar.rviz = warehouse_description 所有/L2）を録画。
#    RViz レイアウトが不足なら L2(sim) へ予告（本レーンは設定を変更しない）。

# 4) 4 provider 切替（DoD: fairness）: Hermes config の active_provider を
#    Claude→GPT→Gemini→Grok で切替え各々 slice2 を確認。
#    比較 run は Memory/session_search OFF（#103 fairness・llm_bridge.py:89-97 起動ガード）。
```

**注入手段の現状**（slice2 が live で未成立な構造ブロック・#156 コメント 2026-06-06）:
pending_tasks の producer 未配線（`situation.py:106` / #102）・current_task は dispatch 受理後 set・
bridge は task topic 非購読 → **対向タスクの注入経路と Mode-A プロンプトは #181（L4）**。
決定論 yield は `#153` 実証済・本 harness は**配線の正しさ**まで（live 判断は #181 land 後）。

## FINDING（→ L4 #181 / #4・本レーンは fix しない＝scheduler.py は L4 所有）

`HermesClient.decide()` は**非JSON/散文ラップ応答**で `ValueError` を投げる（その明文契約
「malformed body → ignore this cycle」＝`llm_client.py:36-44` / `hermes_client.py:55-70`）。
だが `scheduler.run_cycle` は `ValueError` を **`Command.model_validate` の周りでしか catch せず
`decide()` の周りで catch しない**（`scheduler.py:162-178`）→ `ValueError` が cycle を**伝播**し、
commander スレッドを落としうる（`llm_bridge._run_loop` は `CancelledError` のみ suppress）。
JSON を ``` fence で包む“おしゃべりな”LLM が slice3 live でこれを踏む。
`test_nonjson_reply_surfaces_scheduler_robustness_gap` が現挙動を pin（CI 可視・L4 修正で trip→
修正後は `assert forwarder.requests == []` へ反転）。**修正は L4 の仕事**（編集境界外）。
