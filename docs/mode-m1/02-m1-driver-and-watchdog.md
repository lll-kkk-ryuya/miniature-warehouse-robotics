# m1_driver serial node と watchdog 多層停止設計（L0' 結線 = G-l / G-g）

> **Status**: 設計 doc。§2 ①②③＋§3 W-1/W-2 は PR #550（`93bfc93`・2026-08-26 merge）で land — [setup.py](../../ws/src/warehouse_m1_driver/setup.py) の `console_scripts` に `m1_driver` / `m1_probe` を結線・全 dispatch が `clamp_body_velocity` 必経（`M1DriverCore.on_cmd_vel`・R-26 unit = `tests/unit/test_m1_driver_core.py`）。**§2 ④⑤は vendor `Rosmaster_Lib` 委譲（backend seam）・⑥ encoder odom は後続スライス＝未実装。実機動作確認（M0-M2 ゲート・G-g 抜線試験）も未実施**（[03 §1](03-joystick-teleop-bringup.md) に実施記録なし・2026-08-30 時点）。
> **layer**: **L0'**（ホスト側シリアルドライバ送信直前クランプ）。L1/L2/L3/L4 の契約には触れない。
> **オペレーター指示（2026-08-26）**: 「watchdog は必ず入れる」— §3 がその設計。

## 1. 前提事実（裏取り済）

### 1-1. L0' クランプは実装済・結線済み（#550・2026-08-26）

- `clamp_body_velocity(vx, vy, wz)` は実装済（ベクトルクランプ・方向保存・非有限 fail-safe）・R-26 unit + mutation 済（[warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md)）。**呼ぶ実行体 `m1_driver` も PR #550 で land 済**（[mode-x-er/10:491 G-l](../mode-x-er/10-room-scale-safety-review.md) = land 済。dispatch 必経は `M1DriverCore.on_cmd_vel` ＋ `tests/unit/test_m1_driver_core.py` で pin）。
- 方向保存が必須な理由: 軸独立クランプは対角 √(0.3²+0.3²)=0.424 m/s で上限 41% 超過（C-8 = [02:373](../shared/02-hardware-design.md)）。

### 1-2. STM32 側に communication watchdog は無い（ソース調査・2026-08-26）

agent-team 調査（一次情報 = 工場 STM32 ファーム Rosmaster V3.5.1 C ソース実見。出典: <https://github.com/Inouye165/Yahboom-Robot-Expansion-Board-V3.0> `software/firmware/STM32_Firmware/Source/` — 参照日 2026-08-26）:

| 問い | 答え |
|---|---|
| 「一定時間 valid command が来なければ止める」機構 | **無い**（`protocol.c` に timeout / last_cmd_time の類が皆無） |
| IWDG（独立ウォッチドッグ） | **コンパイル時無効**（`config.h` `#define ENABLE_IWDG 0`。有効でも 1s の CPU ハング検出用リセットで通信断は見ない） |
| 唯一の自律停止 | **バッテリー低電圧・過電圧のみ**（復帰はリセットのみ） |
| `Rosmaster_Lib` の heartbeat / keepalive API | **無い**（公開 46 メソッド全走査） |

- **帰結**: ホスト停止・USB 断・プロセスクラッシュで **MCU は最後の速度 PID 目標を保持して走り続ける**（fail-active）。[02:329](../shared/02-hardware-design.md) の「L0' はホストプロセスが生きている間だけ有効」は最悪ケースで確定。
- ⚠️ **実機ファームが調査したソースと同版という保証は無い** → 最終確定は §4 の G-g 実機試験。それまで [02:329](../shared/02-hardware-design.md) の `# TODO(Phase 1)` は維持する。
- **停止経路は 2 本ある**（新規確定）: `set_car_motion(0,0,0)`（ファームの `Motion_Stop(STOP_BRAKE)` に落ちる = [02 V-1 :536](../shared/02-hardware-design.md)）と、別経路の明示 BRAKE **`FUNC_RESET_STATE(0x0F)`**（`Rosmaster_Lib.reset_car_state()`）。停止の二重化に使う（§3 W-2）。

### 1-3. ファームの幾何定数は X3 の値（odom 設計に効く）

- 工場ファームのメカナム幾何は**コンパイル時ハードコード**: `ROBOT_WIDTH 169.0` / `ROBOT_LENGTH 160.11` / `MECANUM_APB 164.555`（同上ソース `app_mecanum.h`・参照日 2026-08-26）。M1 実寸 231.4×284.4mm（[02:302](../shared/02-hardware-design.md)）と乖離していれば **`wz` の指令・報告がスケールずれ**する。ホストから変更不可。
- 帰結①: ファーム報告の車体速度（`vel_raw` 相当・`FUNC_REPORT_SPEED 0x0A`）は同じ誤差を持つ → **odom の入力にしない**。
- 帰結②: **`FUNC_REPORT_ENCODER 0x0D` の生カウント（int32×4・25Hz）だけがこの定数を経由しない** → 自前 odom はここから M1 実測幾何で組む（[02 V-4 :562](../shared/02-hardware-design.md) の方針と一致）。
- 帰結③: 指令側（`FUNC_MOTION 0x12` → ファーム内 IK）は逃げられない → `wz` 補正係数の要否を実測で確定（[03 §2](03-joystick-teleop-bringup.md) プローブ）。

## 2. driver node 設計（Phase 1・Python・ament_python のまま）

```
/bot1/cmd_vel (geometry_msgs/Twist)
      │ subscribe
      ▼
┌─────────────────────────────────────────┐
│ m1_driver node（本スライスで新設）         │
│  ① 非有限 → (0,0,0) fail-safe            │
│  ② clamp_body_velocity(vx, vy, wz) 必経  │ ← L0'（迂回経路を作らない）
│  ③ W-1 freshness timeout（§3）           │
│  ④ int16(v*1000) 変換 → FUNC_MOTION 0x12 │ （payload 先頭 = car_type バイト。#550 は vendor `Rosmaster_Lib` へ委譲＝自前フレーミングしない）
│  ⑤ serial write（CH340 115200 8N1）      │
│  ⑥ 0x0D 受信 → エンコーダ差分 odom        │ （M1 実測幾何・X3 定数を使わない。#550 では未実装＝後続スライス）
└─────────────────────────────────────────┘
```

- 上限値は `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY` を**単一ソース import**（[02:327](../shared/02-hardware-design.md)。現契約値 0.3 のまま = [ADR-0010:22](../adr/0010-raise-speed-cap-to-platform-max.md) の docs 先行原則）。
- **TF は出さない**（`odom→base_link` は ekf_node 単一所有 = [23:163](../architecture/23-perception-and-localization.md)。odom は topic publish のみ）。
- 採用禁止: `set_speed_limit(0x16)` / `set_imu_adjust(0x17)` は推測 API・実装なし（[02 V-1 :536](../shared/02-hardware-design.md)）。
- R-26: 「dispatch 経路が `clamp_body_velocity` を必ず通る」を独立オラクル unit + mutation で pin（= [mode-x-er/10:491 G-l](../mode-x-er/10-room-scale-safety-review.md) の条件 (ii)）。unit は `tests/unit/` に置く（CI 可視性 = [warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md) の教訓）。

## 3. watchdog 多層停止設計

| 層 | 機構 | 守る故障 | 状態 |
|---|---|---|---|
| **W-1** | driver 内 **cmd_vel freshness timeout**: 上流からの最終受信から T 秒で自発的にゼロ送出（brake） | 上流（Nav2 / teleop / joy）の沈黙・ハング | **実装済（#550）**: T は ROS param 注入・既定 0.5s（`DEFAULT_CMD_TIMEOUT_S`＝[twist_mux.yaml:44](../../ws/src/warehouse_bringup/config/twist_mux.yaml)（凍結契約）整合・非正/非有限 param は既定へ fail-safe）。運用値の実機確定は `# TODO(Phase 1 実測)` 継続 |
| **W-2** | **atexit / SIGINT / SIGTERM handler で stop フレーム必送**: `0x12` ゼロ + `0x0F` の**二重送出** | driver の正常・準正常終了（Ctrl-C・例外死） | **実装済（#550）**: `M1DriverCore.shutdown_sequence`（冪等・一度だけ）＋ `driver_node` の atexit / SIGINT / SIGTERM 配線。R-26 unit 済（`tests/unit/test_m1_driver_core.py`） |
| **W-3** | MCU 側 communication watchdog | ホスト kernel 死・USB 断（W-1/W-2 が動けない故障） | **stock では埋められない（不在確定 = §1-2）— [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)（accepted 2026-09-09）の vendor V3.6.5 additive 追記で埋める**。実装は前提ゲート 4 点（U-5 / ライセンス / stock hex 書き戻し実証 / G-g）全通過後・書込までは不在のまま |
| **W-4** | **運用**: 実施者がバッテリー主電源カットオフに手を掛けたまま実施・初回試験は車輪を完全に浮かせる | 全層失敗（W-3 不在の代替） | [mode-x-er/10:376 P-1](../mode-x-er/10-room-scale-safety-review.md)（物理停止手段の到達性）を**必須化**する根拠が §1-2 で確定 |

- W-1 と W-3 は**守る場所が違う**（W-1 = ホスト内・W-3 = ホスト死そのもの）。W-3 が実機で閉じるまで（[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の追記が G-g 試験まで完了するまで）、**W-4 は省略可能な備えではなく必須の層**である（緩和は自動でなく G-h の明示裁定 = ADR-0013 Decision 5）。
- 将来 ros2_control 化しても W-2/W-4 は不変・W-1 は `reference_timeout` 相当へ移る（§5）。

## 4. G-g 実機確認手順（5 分・車輪浮かせ必須）

> [mode-x-er/10:486 G-g](../mode-x-er/10-room-scale-safety-review.md) を閉じる手順。**床走行では絶対にやらない**（暴走が前提の試験）。

| # | 手順 | 観察 → 判定 |
|---|---|---|
| 0 | 車体を台に載せ**4 輪を完全に浮かせる**。周囲 1m に人・物なし。**バッテリー主電源カットオフに手を掛けたまま** | — |
| 1 | 最小速度の走行指令を送る単発スクリプトを**常駐させずに**実行（スクリプト終了 = ホスト送信停止） | 車輪が**回り続ける** = watchdog 無し（ソース調査どおり）／3 秒以内に止まる = watchdog あり |
| 2 | 走行指令中に **USB を物理的に抜く**（拡張ボード側） | 同上。**ここが本命** |
| 3 | USB を挿し直し `reset_car_state()`（`0x0F`）で停止 | `0x0F` 経路の生存確認（W-2 の実証） |
| 4 | ついでに `get_car_type()` / `get_version()` / `get_motor_encoder()` を読む | [03 §2](03-joystick-teleop-bringup.md) プローブと同一セッションで消化 |
| 記録 | 結果を [02:329](../shared/02-hardware-design.md) の TODO 解消として doc02 へ追記（別 PR）＋ [mode-x-er/10 §11](../mode-x-er/10-room-scale-safety-review.md) G-g 行の状態更新 | — |

## 5. ros2_control との関係（未裁定・ADR-0011 予定）

- オペレーター意向（2026-08-22 会話）: ros2_control 方式の採用。ただし検証（2026-08-26・一次情報）で以下が確定:
  1. `mecanum_drive_controller` は **Humble に released**（2.54.0・arm64 apt バイナリ実在。<https://index.ros.org/p/mecanum_drive_controller/> — 参照日 2026-08-26）。
  2. **STM32 に 4 輪個別の閉ループ指令口は無い**（ファーム内 `Motion_Set_Speed` は `protocol.c` でコメントアウト = シリアル未到達。§1-2 と同ソース）→ hardware_interface `write()` は FK で body 速度へ畳み直して `0x12` を送るしかない（controller の IK と往復相殺）。
  3. **Humble に速度クランプ強制機構が無い**（`enforce_command_limits` は Rolling のみ・joint limiter 実装も Rolling のみ）→ クランプは自前 `write()` 内が必然 = **L0' 維持の追認**。
- 帰結: command 側の寄与ゼロ・**feedback 側（4 輪 encoder state → 標準 odometry）に価値** → **Phase 1 は本 doc の Python driver、ros2_control は Phase 2 TARGET 候補として ADR-0011 で裁定**（本 doc は先取りしない）。

## References

- [shared/02-hardware-design.md](../shared/02-hardware-design.md)（:325-329 残課題 7 = L0' 方針 / :536 V-1 / :562 V-4 / :373 C-8）
- [mode-x-er/10-room-scale-safety-review.md](../mode-x-er/10-room-scale-safety-review.md)（:486 G-g / :491 G-l / :376 P-1 / :470 判定サマリ）
- [warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md)（clamp 実装・R-26・テスト配置）
- [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)（速度上限の再定義・L0' 結線が contract PR の前提条件）
- [architecture/23-perception-and-localization.md](../architecture/23-perception-and-localization.md)（:163 TF 単一所有）
- 一次ソース: Yahboom 拡張ボード V3.0 ファーム/ライブラリ（<https://github.com/Inouye165/Yahboom-Robot-Expansion-Board-V3.0>）— 参照日 2026-08-26。実機ファーム版との一致は G-g で確認（U-5）

---

## 【2026-09-12 追補】vendor FW V3.6.5 の屋外速度に効く挙動 5 点と再ビルド toolchain の実体（Mode Outdoor からの forward link）

出典: 取得済み `ROS-Driver-Board-FW-master.zip`（[shared/02:797](../shared/02-hardware-design.md:797)・repo 外）の `Source/` を実 Read（2026-09-12）。屋外運用への含意と車輪径の裁定は [mode-outdoor/07 §8](../mode-outdoor/07-drivetrain-and-wheel-sizing.md) が持ち、本追補は**車体側の事実**だけを置く（§1-2 の「通信途絶停止なし・`ENABLE_IWDG=0`」に追加する形）。

| # | 事実 | file:line | 帰結 |
|---|---|---|---|
| 1 | **低電圧はラッチ式ハード停止**: 9.6 V 以下（`return 96`）または 13.0 V 以上を `BAT_CHECK_COUNT 20` × 100 ms = **2 秒**連続で検出すると `g_system_enable = 0`。コメントどおり**リセット（電源再投入）でしか復帰しない**。6.5〜8.5 V の読みは「電池非装着」として無視 | `app_bat.c:11,13,58,83,94,108` | W-1〜W-4 とは別系統の**停止**。停動 4 A × 4 輪 = 16 A（非安定化レール定格 4 A・[shared/02:318](../shared/02-hardware-design.md:318)）で起こりうる。走行中の電圧監視（[shared/02:448](../shared/02-hardware-design.md:448) の `0x0A` 自動レポート）でこの閾値との距離を見る |
| 2 | **ゼロ指令は短絡ブレーキ**: `Mecanum_Ctrl(0,0,0)` は即 `Motion_Stop(STOP_BRAKE)`（惰行 = `STOP_FREE` ではない） | `app_mecanum.c:34-38` | W-2 の二重停止（`set_car_motion(0,0,0)` + `0x0F`）はこの挙動を**使う**（変えない）。大径輪・高速時の通常停止はホスト側ランプダウンが要る |
| 3 | **yaw-adjust ビット**: `ENABLE_YAW_ADJUST 1` は常時有効だが、発動はホストが `FUNC_MOTION` の car_type バイト bit 0x80 を立てたときだけ。有効時は IMU ヨー PID が左右輪に `∓g_offset_yaw` を足し、**指令 wz は目標に反映されない** | `config.h:26` / `protocol.c:525,534` | `m1_driver` は **0x80 = 0** を送る（現行 backend seam の確認事項・R-26 pin 候補） |
| 4 | **通信途絶停止は無い（§1-2 と一致）**: `ENABLE_IWDG 0` かつ `IWDG_Init()` は宣言のみで呼び出し 0 件。SBUS 経路の `stop_count = 100` は**フレーム到達中の中立デバウンス**であり、リンク断 watchdog ではない | `config.h:17` / `bsp_wdg.h:5` / `app_sbus.c:122,151-170` | [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) の追記は SBUS 経路の `Motion_Stop(brake)` + カウンタ手法を雛形にできる |
| 5 | **再ビルドの実体**: 配布 zip のプロジェクトは **Keil MDK（`rosmaster.uvprojx`・`<ToolsetName>ARM-ADS`・`<uAC6>0` = ARM Compiler 5）のみ**。Makefile / CMake / STM32CubeIDE / IAR プロジェクトは**無い**。`output/rosmaster_V3.6.5.hex` は Intel HEX テキスト 256,343 B（バイナリ換算 ≈ 86〜89 KiB・推定）で **MDK-Lite の 32 KB 制限を超える** | `rosmaster.uvprojx` / `output/` | 再ビルドには**有償 MDK-ARM + レガシー AC5**、または **arm-none-eabi-gcc への移植**（StdPeriph + FreeRTOS + `startup_stm32f10x_hd.s` の armasm 構文書き直し）が要る。書込は UART ISP（USART1 = PA9/PA10・`bsp_usart.c:72,78`・BOOT0）で可 = ADR-0013 Context と整合。**ADR-0013 前提ゲート (c) に toolchain 調達を加える提案（未裁定・`OQ-OD75`）**。ADR-0013 が引く公式 wiki の「STM32CubeIDE」は開発環境の一般案内で、配布ソースの実体は Keil |

- **車輪大径化（[mode-outdoor/07 §9 案 A](../mode-outdoor/07-drivetrain-and-wheel-sizing.md)）に FW 再ビルドは不要**: FW の速度は `speed_mm = Δcounts × 100 × circle_mm / circle_pulse`（`app_motion.c:245`・M1 type = 251.327 mm / 2464 counts）で数えるため、実車輪径 D に対し **実速度 = FW 換算 × D/80 mm** の純スケール。各輪 clamp 700 は**車輪 167 rpm** の上限として残る（144 mm で 1.26 m/s・150 mm で 1.31 m/s）。ホスト側の車輪スケール k（[GLOSSARY §12](../GLOSSARY.md)）で吸収し、odom は `0x0D` 生カウント + 真の周長（§2 ⑥ の後続スライスで param 化）。
- backlink: [mode-outdoor/07](../mode-outdoor/07-drivetrain-and-wheel-sizing.md)（§8）/ [mode-outdoor/06 §3](../mode-outdoor/06-hardware-delta-and-base-selection.md) / [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)

---

## 【2026-09-13 追補②】⑥ encoder odom ＋ 車輪スケール k の実装スライス（branch `feat/m1-wheel-scale-odom`・PR pending）

§2 ⑥「0x0D 受信 → エンコーダ差分 odom（M1 実測幾何）」と [mode-outdoor/07 §9 案 A'](../mode-outdoor/07-drivetrain-and-wheel-sizing.md)（150 mm 通常輪・k = D/80 mm）を `warehouse_m1_driver` に実装した（L0'・package-local・既定挙動 bit 等価・R-26 unit 152 本〔wheel_scale 64 / odom 51 / 配線 AST 37〕・mutation 14/14 KILLED）。

| param | 既定 | 意味 |
|---|---|---|
| `wheel_scale` | 1.0 | clamp（実単位・凍結契約不変）の**後**に wire = 実速度 ÷ k。範囲 [1.0, 2.5] 外・非有限は **fail-closed**（全 command・全 tick が brake。1.0 fallback は 150 mm 装着時に fail-open になるため採らない） |
| `yaw_scale` | 1.0 | wz のみ追加補正（FW 混合定数 APB 189.5 と実効輪距の差）。範囲 [0.2, 5.0] |
| `lateral_enabled` | True | False で vy を clamp の前に 0（通常輪） |
| `odom_enabled` | False | True で `/bot{n}/odom`（`nav_msgs/Odometry`・[doc03:77](../architecture/03-software-architecture.md:77)）を publish。**TF は出さない**（§2 ⑥「TF は出さない」・[doc23:163](../architecture/23-perception-and-localization.md:163)） |
| `wheel_diameter_m` / `counts_per_rev` | 0.080 / 2464 | 真の周長 × `0x0D` 生カウントで積分（FW 報告速度 `0x0A` は使わない＝§1-3 帰結①②） |
| `track_m` / `wheel_signs` | 0.194 / [1,1,1,1] | **PROVISIONAL**（導出値・符号未確認）。G-W1・`m1_probe` で確定 |
| `odom_period_s` / `odom_twist_cov` / `odom_pose_cov` | 0.04 / 0.02 / 1e3 | FW 25 Hz 報告・共分散は暫定 |

- backend seam に `read_encoders()`（vendor `get_motor_encoder()`・失敗は None＝その周期は publish しない。偽のゼロ速度を EKF に食わせない）。
- モータ順は FW `Motion_Set_Speed(L1, L2, R1, R2)` = m1 前左・m2 後左・m3 前右・m4 後右。差動積分は左 = mean(m1, m2)・右 = mean(m3, m4)。
- 運用値（k = 1.875・`wheel_diameter_m` 0.150・`lateral_enabled` False・`odom_enabled` True）は bringup/launch の param 注入で入れる（bringup 所有・別 PR）。契約 `MAX_LINEAR_VELOCITY` の実単位再 pin は contract PR（`OQ-OD71`）。→ **実体 = [`ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml`](../../ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml)（本 doc 末尾 追補③）**。
- backlink: [mode-outdoor/07 追補③](../mode-outdoor/07-drivetrain-and-wheel-sizing.md) / [warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md)（2026-09-13 節）

---

## 【2026-09-16 追補③】運用値の注入先 = `warehouse_bringup/config/m1_wheel_plain150.yaml`（bringup 所有・既定は不変）

追補② の `:135` が「bringup 所有・別 PR」とした**運用値の置き場所を確定**する。実装（PR #676）は driver 既定を stock 80 mm と bit 等価に保ったままなので、**150 mm 換装後にどこで値を入れるか**が唯一の残問だった。

### ③-1. 置き場所と、そこにした理由

| 決めたこと | 内容 |
|---|---|
| 実体 | **[`ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml`](../../ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml)**（新規 1 ファイル） |
| 所有 | bringup。ROS ノードの param は `warehouse_bringup/config/` に集約（[doc16:125](../architecture/16-repository-and-conventions.md:125)-126）・**1 ファイル 1 責務**（[doc16:202](../architecture/16-repository-and-conventions.md:202)）＝新規ファイルなので他トラックと衝突しない（[.claude/rules/parallel-workflow.md:185](../../.claude/rules/parallel-workflow.md)） |
| `config/<env>/` に**置かない** | env overlay は**環境差分**（sim/実機・Hermes 接続先・runtime dir・`traffic_mode` 既定）を書く場所（[doc19:54](../architecture/19-environments-and-config.md:54)）。**どの車輪が付いているかはハードウェアの事実**で、同じ機体なら dev/stg/prod で同一。env に置くと「dev だけ 1.875 倍で走る」という偽の差分を作る |
| 既定の不変性 | このファイルは**明示的に渡さない限り読まれない**。素の `ros2 run warehouse_m1_driver m1_driver`（[03:103](03-joystick-teleop-bringup.md:103)）は**今日と bit 等価**のまま |

### ③-2. 換装後のコマンド（これを打つまで何も変わらない）

```bash
ros2 run warehouse_m1_driver m1_driver --ros-args \
  --params-file "$(ros2 pkg prefix warehouse_bringup)/share/warehouse_bringup/config/m1_wheel_plain150.yaml"
```

- **launch で自動注入しない**のは意図的。`m1_driver` の起動は**車輪に通電する行為**であり、`warehouse_teleop/launch/m1_teleop.launch.py` も同じ理由で driver を起動しない（W-4＝本 doc `:65` / `:76`）。「teleop を上げる」が「車輪を回せる状態にする」を意味しない境界を、この param 注入でも崩さない。
- M0-M2 は standalone（Nav2 / twist_mux を立てない＝[03:50](03-joystick-teleop-bringup.md:50)）なので、注入点は**手打ちコマンドのこの 1 箇所だけ**。

### ③-3. 適用してよい条件（2 つとも満たすまで使わない）

1. **150 mm 車輪が物理的に付いている**こと。逆（150 mm 装着なのに未適用）が危険側で、指令の 1.875 倍で走りながら clamp は 0.3 m/s と表示する＝fail-open（[07:252](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:252) が k の範囲外を fail-closed にした理由）。
2. **[07 §10](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:190) の G-W ゲート**を通していること（G-W1＝`track_m` 確定 / G-W4 UMBmark＝k・`yaw_scale` 校正 / `m1_probe`＝`wheel_signs` 確定＝[07:270](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:270)）。それまで本ファイルで publish される odom は **PROVISIONAL**（bring-up 観測用であり、その上で航法しない）。

### ③-4. 入っている値・入っていない値

- **入っている**: `wheel_scale` 1.875 / `wheel_diameter_m` 0.150 / `lateral_enabled` false / `odom_enabled` true（追補② 表 `:125`-`:128` の 150 mm 値）＋ `counts_per_rev` 2464.0（換装で変わらない FW 定数。**float 必須**＝declared type が DOUBLE のため `2464` では起動時に型不一致で落ちる）＋ `yaw_scale` 1.0（`# TODO(実測)` G-W4 / G-W9）。
- **入っていない**: `track_m` / `wheel_signs` / `odom_period_s` / `odom_twist_cov` / `odom_pose_cov`。追補② `:130`-`:131` が **PROVISIONAL** としたものを転記すると「書いてある＝測った」と誤読され、G-W1 後の更新漏れ箇所が 2 つに増える。driver 既定のままにする。
- **144 mm への退避**（[07:243](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:243) `OQ-OD77`）は本ファイルの 2 値のみ変更（k 1.8 / 径 0.144）。片方だけ変える事故は unit が赤にする。

### ③-5. 検証

- `tests/unit/test_m1_wheel_plain150_profile.py`（R-26・16 本）: k = 0.150/0.080 の独立再計算・k と径の整合（退避の片側編集を検出）・全キーが `driver_node.py` の `declare_parameter` 実体に存在（AST 走査。ROS 2 は未宣言キーを拒否して起動失敗する）・`counts_per_rev` が float・PROVISIONAL 値の不在・実 `M1DriverCore` で `config_error` なし ∧ `hypot(wire)×k ≤ MAX_LINEAR_VELOCITY`・実 `WheelOdometry` が幾何を受理（1 回転＝π×0.150 m）。mutation 10/10 KILLED。
- backlink: [mode-outdoor/07 追補④](../mode-outdoor/07-drivetrain-and-wheel-sizing.md) / [03 追補（2026-09-16）](03-joystick-teleop-bringup.md) / [warehouse_bringup/CLAUDE.md](../../ws/src/warehouse_bringup/CLAUDE.md) / [warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md)
