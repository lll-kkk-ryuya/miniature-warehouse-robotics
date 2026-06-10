# 05 セッション引き継ぎメモ

> コンテキスト消失後に作業を再開するための短期 handoff。恒久設計は
> `docs/architecture/` / `docs/mode-*` / `docs/shared/` を正本とし、この文書は
> 「直近の実行状態・検証済み事実・次の操作」を残す。

## 2026-06-09: #197 merge 後 slice3 live handoff

### Git / worktree 状態

- #197 `Document slice3 sim startup seeding` は merge 済み。
- merge commit: `a8bf374`。
- main worktree `/Users/kawaguchiryuya/Developer/miniature-warehouse-robotics` は
  `origin/main` fast-forward 済み。
- slice3 live worktree `/Users/kawaguchiryuya/Developer/mwr-slice3-live` は
  remote branch 削除後のため `origin/main` detached checkout に合わせた。
- `mwr-slice3-live` コンテナは存在し、`/Users/kawaguchiryuya/Developer/mwr-slice3-live`
  を `/ws` に mount している。イメージは `mwr-spike:ready`。

### #197 で main に入った内容

- `scripts/slice3_seed_initialpose.sh`
  - Nav2 lifecycle active 後に `/bot1/initialpose` と `/bot2/initialpose` を publish する。
  - State Cache が購読開始後の pose を受け取るための sim live 補助。
- `scripts/slice3_live_precheck.sh`
  - `pytest -p no:cacheprovider` で別 worktree の cache warning を回避。
  - precheck 後の launch command に `WAREHOUSE_CONFIG_DIR=/ws/config`、
    `WAREHOUSE_ENV=dev`、sim 録画限定
    `WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999`、initialpose re-seed 手順を表示。
- `tests/e2e/README.md`
  - slice3 live runbook に同じ env / initialpose re-seed 手順を追記。

### なぜ必要だったか

- tiryoh container 内で `/ws/ws` から launch すると、既定の相対 `config` が
  `/ws/ws/config` を指し、空 config になって `locations` 欠落で launch が落ちる。
  `WAREHOUSE_CONFIG_DIR=/ws/config` が必要。
- idle sim では AMCL が初期 pose 以外を継続 publish しないことがあり、実機想定の
  `pose_freshness_timeout=1.0` だと録画中に false `pose_stale` が出る。
  これは sim 録画限定で `999` に緩和する。prod default は変更していない。
- State Cache が initial pose を取りこぼす起動順 race があり、Nav2 lifecycle active 後に
  `scripts/slice3_seed_initialpose.sh` を再実行すると `bot1` / `bot2` が揃う。

### 実施済み検証

- `scripts/slice3_live_precheck.sh --offline`
  - `PASS=7 FAIL=0 WARN=0 SKIP=1`
  - SKIP は host に `ros2` が無いため。ROS launch は tiryoh container で実行する。
- `bash -n scripts/slice3_live_precheck.sh`
- `bash -n scripts/slice3_seed_initialpose.sh`
- `python3.12 -m pytest -p no:cacheprovider tests/unit/test_config.py tests/unit/test_bringup_launch.py tests/unit/test_nav2_bringup_launch.py -q`
  - 24 passed, 2 skipped。
  - skipped は host に `launch_ros` が無いため。
- `git diff --check HEAD~1 HEAD`
- `python3 scripts/check_consistency.py`
  - 0 errors。
  - 既存 warning: `docs/STATUS.md` の pinned `origin/main=bb0b636` が古い。
- container spot check
  - `state.json` に `bot1` / `bot2` が揃うことを確認。
  - `pose_stale` は消える。
  - `blocked_timeout` は idle sim でも出うる既知 low-harm 制約
    （`docs/architecture/12-infrastructure-common.md` の blocked limitation）。

### 次にやること

1. #156 に現況コメントを入れる。
   - #181 / #192 / #197 は完了済み。
   - 次は live precheck と full-stack recording。
2. Hermes Gateway `:8642` と Nav2 Bridge `:8645` を起動する。
   - API key / `.env` は読まない。必要な secret 注入は人間操作。
3. main checkout 上で:

   ```bash
   scripts/slice3_live_precheck.sh --live
   ```

4. tiryoh container 内で slice1 health を再確認する。

   ```bash
   export WAREHOUSE_CONFIG_DIR=/ws/config
   export WAREHOUSE_ENV=dev
   export WAREHOUSE__SAFETY__POSE_FRESHNESS_TIMEOUT=999
   ros2 launch warehouse_bringup bringup.launch.py llm:=false sim:=true
   ```

   別 shell で ROS setup 後:

   ```bash
   cd /ws && scripts/slice3_seed_initialpose.sh
   ```

5. full stack 録画検証へ進む。

   ```bash
   ros2 launch warehouse_bringup bringup.launch.py sim:=true llm:=true traffic_mode:=none rviz:=true
   cd /ws && scripts/slice3_seed_initialpose.sh
   ```

6. 記録する値。
   - L6 API p95。2.5s を超えるなら cycle を 4-5s にする判断。
   - Hermes provider ごとの挙動。
   - Nav2 Bridge accepted command。
   - RViz/noVNC 録画可否。
   - `state.json` に両 bot が出るか、`pose_stale` が再発しないか。

### 注意点

- `docs/STATUS.md` の SHA warning は #197 以前から残る既知状態。STATUS refresh PR で解消する。
- 比較 run は Memory/session_search OFF が前提。Bridge 側は intent guard であり、実 OFF の権威は Hermes config。
- #156 本文には古い blocker 記述が残っているため、issue コメントで現況を上書きする。
