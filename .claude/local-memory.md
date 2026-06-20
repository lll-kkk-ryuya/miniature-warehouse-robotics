# Local Memory

> Claude Code 用の短期 handoff。長期の設計正本ではない。設計判断は `docs/` を読むこと。

## 2026-06-09: #197 merge 後の再開メモ

- #197 `Document slice3 sim startup seeding` は merge 済み。
- merge commit は `a8bf374`。
- main worktree は `origin/main` へ fast-forward 済み。
- `/Users/kawaguchiryuya/Developer/mwr-slice3-live` は `origin/main` detached checkout に合わせ済み。
- `mwr-slice3-live` container は `/Users/kawaguchiryuya/Developer/mwr-slice3-live:/ws` を mount している。

### #197 の要点

- `scripts/slice3_seed_initialpose.sh` を追加。
  - Nav2 lifecycle active 後に `/bot1/initialpose` / `/bot2/initialpose` を publish。
- `scripts/slice3_live_precheck.sh` と `tests/e2e/README.md` に以下を追加。
  - `WAREHOUSE_CONFIG_DIR=/ws/config`
  - `WAREHOUSE_ENV=dev`
  - sim 録画限定 `WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999`
  - lifecycle active 後の `cd /ws && scripts/slice3_seed_initialpose.sh`

### 重要な観測

- `/ws/ws` から launch すると既定相対 `config` が `/ws/ws/config` を指すため、
  `WAREHOUSE_CONFIG_DIR=/ws/config` が必要。
- idle sim では AMCL が継続 pose publish しない場合があり、実機用 default
  `pose_freshness_timeout=1.0` では false `pose_stale` が出る。
- `pose_stale` は sim 録画限定 override + initialpose re-seed で消えた。
- `blocked_timeout` は idle sim で出うる既知 low-harm 制約。これ自体は今回の blocker ではない。

### 済み検証

- `scripts/slice3_live_precheck.sh --offline` => `PASS=7 FAIL=0 WARN=0 SKIP=1`
- `bash -n scripts/slice3_live_precheck.sh`
- `bash -n scripts/slice3_seed_initialpose.sh`
- `python3.12 -m pytest -p no:cacheprovider tests/unit/test_config.py tests/unit/test_bringup_launch.py tests/unit/test_nav2_bringup_launch.py -q`
  - 24 passed, 2 skipped (`launch_ros` unavailable on host)
- `python3 scripts/check_consistency.py`
  - 0 errors, existing STATUS SHA warnings only.

### 次の作業

1. #156 に「#181/#192/#197 完了、次は live precheck/full-stack recording」とコメント。
2. Hermes Gateway `:8642` と Nav2 Bridge `:8645` を起動。
3. `scripts/slice3_live_precheck.sh --live`
4. tiryoh container で slice1 health:
   - export `WAREHOUSE_CONFIG_DIR=/ws/config`
   - export `WAREHOUSE_ENV=dev`
   - export `WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999`
   - launch `ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true`
   - 別 shell で `cd /ws && scripts/slice3_seed_initialpose.sh`
5. full stack recording:
   - `ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true`
   - `cd /ws && scripts/slice3_seed_initialpose.sh`

### 禁止 / 注意

- `.env` / secrets は読まない。API key 注入は人間操作。
- full-stack 比較 run は Memory/session_search OFF が前提。
- `docs/STATUS.md` の old SHA warning は STATUS refresh PR で扱う。

## 2026-06-20: #308 Mode A v1/v1.5 + Hermes live launcher merge 後メモ

- PR #308 `[codex] add Mode A local negotiation and live launcher` は merge 済み。
- squash merge commit: `2feeb5d add Mode A local negotiation and live launcher`。
- feature head before merge: `65094bc`。
- merge 時点の PR は `CLEAN`、CI pass。
- `/private/tmp/mwr-mode-a-conversation-v1` は merge 後 `main` に fast-forward 済み。

### #308 で main に入った内容

- Mode A v1/v1.5 の局所会話・自己安全行動 scaffold。
  - `ConversationEvent` / `TaskLifecycleEvent` / `ConversationEventLog`
  - `SelfActionGate`
  - whitelist action: `wait_self` / `yield_to_retreat_A` / `yield_to_retreat_B` / `release_route_lock`
  - persona/model payload は `from_persona_payload()` で Bridge が `expires_at` と `state_ref.gen_id` を stamp する。model 自己申告の freshness は信用しない。
- Scheduler / Bridge の lifecycle logging。
  - accepted dispatch 後の telemetry 書き込みは fail-open。
  - `conversation_events.jsonl` の書き込み失敗で commander loop を落とさない。
- WO 側 conversation metrics。
  - `autonomy_ratio`
  - `commander_override_rate`
  - `agreement_latency`
  - `local_resolution_rate`
  - `communication_efficiency`
  - `contract_violation_rate`
  - `safety_margin_after_agreement`
  - 実 producer 形（`conversation_event` / `task_lifecycle` / `self_action_result`）から episode を導出。
  - 同一 episode に複数 proposal review がある場合、episode-only `commander_override` lifecycle row で override を過大計上しない。
- Hermes + Gazebo live launcher。
  - `deploy/dev/check-hermes-live.sh`
  - `deploy/dev/run-mode-a-live.sh`
  - `--start-hermes`
  - Docker-on-Mac の `host.docker.internal` 変換。
  - `API_SERVER_KEY` / `HERMES_API_KEY` が両方 present で不一致なら起動前に fail。
  - macOS `--start-hermes` は launchd user env に `API_SERVER_ENABLED` / `API_SERVER_KEY` / `HERMES_API_KEY` を入れて Hermes service を起動する。不要時は `launchctl unsetenv API_SERVER_KEY` / `launchctl unsetenv HERMES_API_KEY`。

### ローカルで確認済みの Hermes 状態

- 根本原因: `config/dev/.env` 内で `API_SERVER_KEY` と `HERMES_API_KEY` が不一致だった。
- 値は表示せず、`config/dev/.env` と `~/.hermes/.env` の `API_SERVER_KEY` / `HERMES_API_KEY` を同一 token に同期済み。
- Hermes Gateway 再起動済み。
- 別 session 相当の clean zsh（`API_SERVER_KEY` / `HERMES_API_KEY` unset）でも live preflight 通過済み。
  - Bridge env file exists
  - token present hidden
  - `/health` pass
  - authenticated `/v1/models` pass
  - `Hermes live preflight complete`

### #308 merge 前の検証

- targeted unit:
  - `tests/unit/test_bridge_scheduler.py`
  - `tests/unit/test_conversation_events.py`
  - `tests/unit/test_self_action_gate.py`
  - `tests/unit/test_conversation_metrics.py`
  - 79 passed。
- full pytest: 1125 passed, 9 skipped。
- `ruff check .` pass。
- `ruff format --check .` pass。
- `bash -n deploy/dev/check-hermes-live.sh deploy/dev/run-mode-a-live.sh` pass。
- `python3 scripts/check_consistency.py`: 0 errors, 4 warnings。
  - warnings は既存の `docs/STATUS.md` origin/main pin 古さのみ。
- GitHub CI pass。

### 次にやること

1. 最新 main で Gazebo + Hermes + LLM Bridge の live 起動を確認する。
   - 標準:
     ```bash
     deploy/dev/run-mode-a-live.sh --start-hermes
     ```
   - provider call まで意図的に確認する場合だけ `--chat` を付ける。
2. ブラウザ/noVNC で Gazebo 表示を確認する。
   - 既定 container: `mwr-mode-a-live`
   - 既定 noVNC port: `6082`
   - 既存 container を使う場合は `MWR_SIM_CONTAINER` / `MWR_SIM_PORT` を明示。
3. LLM Bridge が Hermes へ 401 なしで接続し、`conversation_events.jsonl` が出ることを確認する。
   - 401 が出たらまず `deploy/dev/check-hermes-live.sh --skip-container`。
   - `API_SERVER_KEY and HERMES_API_KEY are both set but differ` が出たら、値を表示せず `config/dev/.env` と `~/.hermes/.env` を同期する。
4. Gazebo 実走で Mode A v1/v1.5 の観測を取る。
   - task lifecycle row が出るか。
   - local agreement / self_action_result が出るか。
   - WO metrics が 0/None に潰れず計算できるか。
5. 次の実装候補。
   - UI/web console で `conversation_events.jsonl` を live 表示する。
   - SelfActionGate の実 action dispatcher を Gazebo/Nav2 実経路へつなぐ。
   - task lifecycle producer を demo seed だけでなく task source / orchestrator 側へ拡張する。
   - Mode A v2: bot 間局所交渉を「observer/critic の commander」に上げず、必要時だけ commander review に回す。
   - `launchctl setenv` に置いた token の cleanup helper を追加するか検討する。

### 注意

- `deploy/dev/check-hermes-live.sh --chat` は provider quota を使う。
- `config/dev/.env` と `~/.hermes/.env` は git 管理外。値をログや PR に出さない。
- STATUS の SHA warning は今回の feature とは別の STATUS refresh で扱う。
