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
> 申し送り: doc16 §9 branch 表（16-...:72）は warehouse_teleop を `ros2/hw` 表記＝`feat/teleop`（#158）追記は governance/docs PR（doc16 は skeleton 所有・本レーン read-only）。

## 【2026-08-26 追記】joystick teleop（`teleop_joy`）追加

設計正本: [docs/mode-m1/03-joystick-teleop-bringup.md](../../../docs/mode-m1/03-joystick-teleop-bringup.md) §3（joy 経路・Yahboom 公式 joy node 不採用の理由）。

- **提供 (produce)**: console_script **`teleop_joy`**（`teleop_joy.py`）— `/joy`（`sensor_msgs/Joy`・stock `joy_node` 出力）購読 → pure **`joymap.joy_to_twist`** → `/<bot>/cmd_vel`。keyboard 版と同じ **standalone bring-up utility**（mux 入力追加は bringup 所有の別変更のまま）。固定レート republish（デッドマン保持中=指令・解放=明示ゼロ。m1_driver W-1 との整合）。
- **`joymap`（pure・rclpy 非依存）**: **ベクトルキャップ**（`hypot(vx,vy) ≤ min(param, MAX_LINEAR_VELOCITY)`・方向保存＝**C-8**。keymap のスカラー clamp では `vy` を扱えないための新設）・デッドマン必須・非有限→0 寄与・cap 負/非有限→fail-stop（keymap `_nonneg` idiom）。軸/ボタン index は公式レイアウト既定（x=axes[1], y=axes[0], yaw=axes[2], deadman=button 4）で **ros param 上書き可**（実機 `jstest` で確定＝mode-m1/03 §2-3）。
- **消費 (consume)**: `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY` / `clamp_velocity`（単一ソース）・sensor_msgs / joy（package.xml exec_depend 追加）。
- **テスト (R-26)**: `tests/unit/test_teleop_joymap.py`（15 ケース・spec 由来オラクル）。**mutation 2 本 KILLED**（2026-08-26 実測）: per-axis クランプすり替え→1 fail（C-8 本命）・デッドマン disarm→1 fail。クリーン 0 fail。
- 注意: ここは**第一防御**にすぎない。最終防衛は `warehouse_m1_driver` の L0'（`clamp_body_velocity`）＝別トラック。

## 【2026-08-31 追記】/joy 鮮度タイムアウト（teleop_joy の freshness dead-man）

設計正本: [docs/mode-m1/03-joystick-teleop-bringup.md](../../../docs/mode-m1/03-joystick-teleop-bringup.md) §3（/joy 鮮度タイムアウトの行）。

- **動機**: Humble joy_node はデバイス喪失時に /joy の publish を止めるだけで**ゼロ Joy を出さない**（joystick_drivers ros2 branch `joy.cpp` handleJoyDeviceRemoved）。旧 `_on_timer` は `self._latest` を無条件 20Hz 再送するため、デッドマン保持中に joy_node/receiver が死ぬと最後の非ゼロ twist を永久送出し、cmd_vel が fresh のまま m1_driver **W-1（0.5s）も発火しない**穴があった。
- **提供 (produce)**: ros param **`joy_timeout_s`**（既定 `DEFAULT_JOY_TIMEOUT_S = 0.6`＝teleop_keyboard `stop_timeout` と同値）。最後の /joy 受信からの経過が超過（strictly greater・keyboard 版と同境界）で republish がゼロ twist に落ちる。初回 /joy 受信前は stale 扱い（fail-closed・driver_core W-1 と同姿勢）。
- **pure 実装**: `joymap.apply_joy_freshness(latest, elapsed_s, timeout_s)`（rclpy 非依存）。**非有限 elapsed は stale 扱い**（`NaN > t` が False で永久ラッチする穴そのものを塞ぐ）・**param の非有限/非正は既定へ fail-safe**（`_positive_or_default`・`driver_core.py:37-41` / keyboard `_positive` と同型。inf timeout は dead-man を無効化するため raw を honor しない）。
- **テスト (R-26)**: `tests/unit/test_teleop_joymap.py` 末尾に 11 ケース append（fresh 素通し / stale→ゼロ / 境界＝ちょうどは素通し / 非有限 elapsed 3 種→ゼロ / 退化 timeout 4 種→既定で裁定 / 既定値 0.6 の keyboard パリティ）。**mutation 4 本 KILLED**（2026-08-31 実測・個別適用）: ①ガード disarm ②非有限 elapsed を fresh 扱い ③timeout ハードニング除去 ④比較反転。クリーン 0 fail・全 suite 2545 passed。
- ⚠️ **mutation ハーネスの追加教訓（pyc エイリアス）**: 実ファイル差替方式でも **`__pycache__` を毎回 purge しないと stale bytecode が復元後も import され続ける**（pyc 有効判定は mtime 秒粒度＋サイズ。同一秒内の書き戻しでエイリアスし、「復元済みソース・変異体の挙動」という偽状態になる—2026-08-31 実測）。ハーネスは `PYTHONDONTWRITEBYTECODE=1` ＋ 各 run 前に `__pycache__` 削除を必須とする（`warehouse_m1_driver/CLAUDE.md` の PYTHONPATH 落とし穴と対の注意）。
