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
| `test_nonjson_reply_is_ignored_no_forward` | 非JSON応答で `decide()` の `ValueError` が出ても、scheduler が cycle ignore として扱い、**0 forward**・loop 生存を保証する（#192）。 |

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
  **Mode-A プロンプト + デモ用 `pending_tasks` seed は #181 で配線済み**。live で本物の
  yield 判断を出す検証は slice3。

## slice3 live demo runbook（実 Hermes / RViz / noVNC 録画）

> **= 3段リリース第1段の素材**（sim 録画版が最初の公開/営業送付可成果物・round 戦略 2026-06-06）。
> **着手の前提**: ① **#181 land 済み**（task 注入 + Mode-A system prompt）
> ② **#192 land 済み**（非JSON応答を cycle ignore）
> ③ **L6 のサイクル長確認**（API p95>2.5s なら 3秒→4-5秒・デモ尺の作り直しを断つ）。
> ①②は host harness で回帰確認、③は live Hermes/provider で測る。

tiryoh コンテナ（host py3.12 では ROS/launch/Gazebo 不可＝`reference_local_gate_execution`）で:

```bash
# -1) host precheck（ROS/network/Gazebo 不要）
scripts/slice3_live_precheck.sh --offline
#   → tests/e2e 回帰 + WAREHOUSE_TASKS seed 検証 + launch command 表示。
#     Hermes/Nav2 Bridge を起動済みなら `--live` で /health も確認する。

# 0) 外部 daemon（launch では合成しない・bringup.launch.py:38-47）
#    Hermes Gateway :8642（dev キー疎通済＝memory project_api_keys_dev_setup）
#    Nav2 Bridge   :8645（REST→BasicNavigator, #86）を別途起動。
#    ⚠ tiryoh container 内から host の Hermes に届かせるには loopback ではなく:
#      export WAREHOUSE__HERMES__BASE_URL=http://host.docker.internal:8642
#      （config override 機構 config.py:28,48-66 が hermes.base_url を上書き）。
#      precheck --live も同様に HERMES_BASE_URL=http://host.docker.internal:8642 を渡して確認する
#      （precheck は container を自動検知し loopback 設定時に host.docker.internal を WARN 提示）。

# 1) slice1 health（upstream 不要・今すぐ可能。DoD step1）
export WAREHOUSE_CONFIG_DIR=/ws/config
export WAREHOUSE_ENV=dev
# sim 録画限定: AMCL が初期 pose 以外を継続 publish しないことがあるため freshness を緩和する。
export WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999
ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true
#   → Nav2 lifecycle 全 bot active / state_cache が state.json を 100ms 書出 /
#     guardian 50ms reflex / llm:=false でも起動（Hermes 無し fallback）を確認。
#   → 別 shell で ROS setup を source し、Nav2 lifecycle active 後に:
#     cd /ws && scripts/slice3_seed_initialpose.sh
#     State Cache が両 bot の pose を取り込むことを確認。

# 2) slice2/3 full stack（#181/#192 land 後）。scenario:=head_on で 200mm 隘路の正面対向 spawn、
#    rviz_config:=record で録画用俯瞰 cfg を選択（bringup が両 arg を sim へ forward＝slice3。
#    無いと berth 横並びを録画してしまう＝デモの核が映らない）。
ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true scenario:=head_on rviz_config:=record
#   sim+nav2+state+safety+nav2_bridge(:211-214 allowlist)+llm を合成（#162）。
#   full stack でも lifecycle active 後に `cd /ws && scripts/slice3_seed_initialpose.sh` を再実行。
#   2台に対向タスク（§9.2 北 staging ↔ 通路A 南端の座標ゴール・route_A はロックキー）を投入し、
#   LLM が 08a:337-359 の yield+wait → MCP → nav2_bridge → Nav2 で最接近 ≥0.15m を計測（11a:446）。

# 3) 録画: rviz:=true ∧ rviz_config:=record で warehouse_sim/rviz/record.rviz（両 footprint+
#    scan+占有 map の俯瞰 cfg。既定 minicar.rviz は最小レイアウト, sim.launch.py:66-75）を選択し、
#    `scripts/slice3_record.sh start` / `... stop` で noVNC/screen-capture をラップ録画する
#    （実キャプチャ＝人間ゲート）。record.rviz は sim 所有＝レイアウト不足は L2(sim) へ予告
#    （本レーンは cfg 自体を変更しない）。

# 4) 4 provider 切替（DoD: fairness）: Hermes config の active_provider を
#    Claude→GPT→Gemini→Grok で切替え各々 slice2 を確認。
#    比較 run は Memory/session_search OFF（#103 fairness・llm_bridge.py:89-97 起動ガード）。
```

**注入手段の現状**:
対向タスクは恒久 producer ではなく **`WAREHOUSE_TASKS` env のデモ用 seed** で注入する（#181）。
`current_task` は dispatch 受理後に scheduler が set し、受理 navigate の `to` 一致で
`pending_tasks` を消費する。恒久 producer は将来 Warehouse Orchestrator #6 / task queue 側で
決める。本 harness は**配線の正しさ**までを証明し、live provider の yield 判断は slice3 で確認する。

## FIXED（#192）非JSON応答 robustness

`HermesClient.decide()` は**非JSON/散文ラップ応答**で `ValueError` を投げる（その明文契約
「malformed body → ignore this cycle」＝`llm_client.py:36-44` / `hermes_client.py:55-70`）。
`scheduler.run_cycle` は `decide()` 由来の `ValueError` / `TypeError` も
`Command.model_validate` 由来の不正 schema と同じ `_on_invalid_response` に流し、cycle ignore とする。
`test_nonjson_reply_is_ignored_no_forward` が **0 forward・loop 生存・Nav2-only 不遷移**を pin する。
