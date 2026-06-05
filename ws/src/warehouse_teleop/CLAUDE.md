# warehouse_teleop — キーボード teleop（動作確認の足場）

- **担当トラック / ブランチ**: track:teleop / `feat/teleop`（#158）
- **Phase**: 1（実機不要・bring-up / sim 手動ドライブ utility）
- **ビルド**: ament_python
- **ノード / モジュール**:
  - `teleop_keyboard`（rclpy ノード・entry point）
  - `keymap`（rclpy 非依存の **pure 写像モジュール**。`warehouse_safety/guard_logic`・`warehouse_nav2_bridge/core` と同 idiom → unit はここを叩く）
- **編集境界**: このパッケージ配下のみ。共有契約 `warehouse_interfaces` は変更不可（`.claude/rules/parallel-workflow.md` §4）。

## 提供 (produce)
- topic: `/<bot>/cmd_vel`（`geometry_msgs/Twist`・bot は ros param、既定 `bot1`・doc03:87）
  - ⚠️ **standalone bring-up utility**：`/<bot>/cmd_vel` を**直接** publish する（sim `ros_gz_bridge` / 実機 base が consume する topic）。**Nav2 + twist_mux を立てずに**使う。フルスタック稼働時は Nav2 path-follower と `/cmd_vel` を奪い合う（Emergency Guardian が prio-100 `/cmd_vel/emergency` 経由にする理由＝doc15）。`/cmd_vel/teleop` mux 入力追加は bringup 所有の別変更＝本レーン scope 外。

## 消費 (consume)
- 契約: `warehouse_interfaces.safety.clamp_velocity` / `MAX_LINEAR_VELOCITY`（safety.py:18,25・**0.3 をハードコードしない**単一ソース）
- 契約: `warehouse_interfaces.config.load_config`（`safety.max_linear_velocity` ≤ ハードキャップで運用上限を下げられる・doc19）
- キー入力（termios raw / no-TTY フォールバック）

## 速度上限 (R-26)
- リニアは `clamp_velocity(v, max_speed)`、`max_speed = min(_nonneg(param), MAX_LINEAR_VELOCITY)`（コード強制で 0.3 m/s を超えない）。非有限要求（NaN/±inf）は 0.0 stop。
- **param ハードニング**: `max_linear/max_angular/linear/angular_step` は `_nonneg`（非有限・負 → 0.0 fail-stop）で正規化。負の cap は対称クランプを反転させ runaway（`clamp_velocity(v,-m)→+m`）、負の step は走行方向を反転させるため、node の param 防御に加え **pure `key_to_twist` 側でも負/非有限の cap・step を 0.0 に潰す**（caller 不問の単一ソース防御・符号反転なし・unit 検証あり）。`publish_rate`/`stop_timeout` は `_positive`（非有限/≤0 → 既定値。NaN stop_timeout が dead-man を無効化するのを防ぐ）。
- アングラに凍結契約は無い（safety.py は LINEAR のみ）→ teleop-local の `max_angular` を同 `clamp_velocity` で bound（非有限→stop の保証だけ流用）。
- **終了**: `q`/`Ctrl-D` は callback で `shutdown_requested` フラグを立て、`main()` の `spin_once` ループが抜けて shutdown（callback 内 `rclpy.shutdown()` 禁止＝executor にマスクされ exit しない・repo idiom）。`Ctrl-C` は SIGINT→`KeyboardInterrupt`→`main()` finally。

## 依存
- `warehouse_interfaces` のみ（他トラック内部を import しない）＋ rclpy / geometry_msgs（exec_depend は package.xml）。

## テスト
- `tests/unit/test_teleop_keymap.py`：pure `key_to_twist` / `decode_key`（ROS spin 不要・headless 安全）。クランプ境界（0.3 / 0.31→0.3 / NaN→0.0 / stop→(0,0)）を `@pytest.mark.safety` で検証（R-26）。Ruff(py312/line100) + pytest 緑を維持。
- no-TTY（`stdin.isatty()` False）でノードは raw 入力を無効化し warn のみ＝CI/headless で落ちない。

## 設計ドキュメント
- `docs/architecture/03-software-architecture.md`（`/bot{n}/cmd_vel` 契約・read-only）/ `15`（twist_mux・doc15）/ `16`・`17`
- `.claude/rules/safety.md`（ミニチュア最大 0.3 m/s 強制）

> #1 契約凍結の雛形 stub を #158 で実装に置換（リポジトリ最後の skeleton stub 解消）。
> 申し送り: doc16 §9 branch 表（16-...:71）は warehouse_teleop を `ros2/hw` 表記＝`feat/teleop`（#158）追記は governance/docs PR（doc16 は skeleton 所有・本レーン read-only）。
