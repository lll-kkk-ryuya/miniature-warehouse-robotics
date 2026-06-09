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
