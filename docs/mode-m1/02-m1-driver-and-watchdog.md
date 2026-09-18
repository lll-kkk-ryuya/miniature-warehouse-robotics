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

### ③-3. 適用条件（1 つだけ）と、odom の使用制限（別の話）

**「いつ適用するか」と「出てきた odom を何に使ってよいか」は別の条件**である。混ぜると、G-W ゲート待ちの間だけ**より危険な状態**（150 mm 装着で未適用）を推奨してしまう。

1. **適用条件 = 150 mm 車輪が物理的に付いていること、それだけ**。装着したら**直ちに渡す**。未適用のまま走らせるのが危険側で、指令の 1.875 倍で走りながら clamp は 0.3 m/s と表示する＝fail-open（[07:252](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:252) が k の範囲外を fail-closed にした理由）。逆に stock 80 mm のまま渡すと 1/1.875 倍で走り odom も 1.875 倍に伸びる（遅い側＝危険ではないが誤り）。
2. **使用制限（適用の条件ではない）**: [07 §10](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:190) の G-W ゲート（G-W1＝`track_m` 確定 / G-W4 UMBmark＝k・`yaw_scale` 校正 / `m1_probe`＝`wheel_signs` 確定＝[07:270](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:270)）を通すまで、本ファイルで publish される odom は **PROVISIONAL**＝**bring-up 観測（値が動くことの確認）にのみ使い、その上で航法しない**。**G-W4 自体が k を校正する試験であり、profile を適用しないと実施できない**（適用は G-W ゲートの前提であって、結果ではない）。ゲート通過後、測定値を反映してから航法に使ってよい。

### ③-4. 入っている値・入っていない値

- **入っている**（5 キー）: `wheel_scale` 1.875 / `wheel_diameter_m` 0.150 / `lateral_enabled` false / `odom_enabled` true ＝ **[07:252](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:252)-256 の「150 mm での値」列**（本 doc 追補② の表は**既定値**の列しか持たない＝`:123`-`:131`。運用値の指示元は `:135`）＋ `counts_per_rev` 2464.0（換装で変わらない FW 定数。**float 必須**＝declared type が DOUBLE のため `2464` では起動時に型不一致で落ちる）。
- **入っていない**: `yaw_scale` / `track_m` / `wheel_signs` / `odom_period_s` / `odom_twist_cov` / `odom_pose_cov`。追補② `:126`・`:130`-`:131` と [07:253](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:253)・`:257`-`:258` が **PROVISIONAL / 実測待ち**としたものを転記すると「書いてある＝測った」と誤読され、G-W1 後の更新漏れ箇所が 2 つに増える。driver 既定のままにする。**`yaw_scale` を特に置かない理由**: 07:253 の 150 mm 値は「実測（G-W4/G-W9）」であって数値ではなく、`1.0` は driver 既定の逐語コピーにすぎない。ここに `1.0` を書くと、G-W4 後に driver 既定へ実測値を入れた瞬間、**この profile が黙って 1.0 へ引き戻す**（上書きが静かに効く）。
- **144 mm への退避**（[07:243](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:243) `OQ-OD77`）は本ファイルの 2 値のみ変更（k 1.8 / 径 0.144）。片方だけ変える事故は unit が赤にする。

### ③-5. 検証

- `tests/unit/test_m1_wheel_plain150_profile.py`（R-26・18 本）: k = 0.150/0.080 の独立再計算・k と径の整合（退避の片側編集を検出）・全キーが `driver_node.py` の `declare_parameter` 実体に存在（AST 走査。ROS 2 は未宣言キーを拒否して起動失敗する）・**キー集合の等値 pin**（部分集合でなく＝`cmd_vel_timeout_s` 等の safety param が後から紛れ込むのを拒否。宣言済なので AST チェックだけでは通ってしまう穴を塞ぐ）・`counts_per_rev` が float・`yaw_scale` と PROVISIONAL 値の不在・実 `M1DriverCore` で `config_error` なし ∧ 上限超指令が**上限で飽和**（`hypot(wire)×k = MAX_LINEAR_VELOCITY`）・実 `WheelOdometry` が幾何を受理（1 回転＝π×0.150 m）。mutation 12/12 KILLED。
- backlink: [mode-outdoor/07 追補④](../mode-outdoor/07-drivetrain-and-wheel-sizing.md) / [03 追補（2026-09-16）](03-joystick-teleop-bringup.md) / [warehouse_bringup/CLAUDE.md](../../ws/src/warehouse_bringup/CLAUDE.md) / [warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md)

---

## 【2026-09-17 追補④】W-3 実装仕様 — ADR-0013 §Open 6 点の具体化（書込前・前提ゲート未通過）

> **layer**: **L0**（MCU 常駐・vendor V3.6.5 への additive 追記）。付随するホスト側の候補変更（④-3 案 β）は **L0'**（`warehouse_m1_driver`）。L1〜L4 の契約には触れない。
> **位置づけ**: [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)（accepted 2026-09-09・Decision 1〜6 = [:25-34](../adr/0013-stm32-command-stream-watchdog.md:25)）を前提に、同 §Open（[:58-63](../adr/0013-stm32-command-stream-watchdog.md:58)）の 6 点 — ライセンス／Mac・Jetson からの ISP ツール／T と W-1 の層間整合／U-5／G-g 手順の詳細／追記ソースの置き場 — へ**実装レベルの案**を置く。[mode-outdoor/07:210](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:210) `OQ-OD75`（toolchain 裁定）の置き場も本追補（④-4）。**MCU への書込は前提ゲート 4 点通過までしない**（ADR Decision 2）。
> **出典**: 取得済み `ROS-Driver-Board-FW-master.zip`（[shared/02:797](../shared/02-hardware-design.md:797)・仕様の一次ソース・repo 外）の `Source/`・`FreeRTOS/`・`rosmaster.uvprojx` を実 Read（2026-09-17）。以下の `protocol.c:NNN` 等は zip 内パス。ホスト側の引用は `origin/main` `d03803c` の実体。

### ④-1. 前提ゲートの現況（2026-09-17）

| ゲート（ADR Decision 2） | 現況 | 残り |
|---|---|---|
| (a) U-5 搭載 FW 版 | **major.minor は一致済**: 2026-09-10 実測 `get_version()` = **3.6**・`car_type` は工場値 0x02 → **0x0A** へ書換え済で完全な電源断をまたいで保持（[03:21](03-joystick-teleop-bringup.md:21) / [03:25](03-joystick-teleop-bringup.md:25) / [ADR-0010 追補 :91-105](../adr/0010-raise-speed-cap-to-platform-max.md:91)）。vendor lib は patch 番号を公開しない | patch レベルの同一性は ④-5 手順 2 の flash read-back と `rosmaster_V3.6.5.hex` の比較で閉じる（RDP 無効の場合のみ可能） |
| (b) ライセンス | zip に `LICENSE` / `COPYING` **0 件**・`README.md` は 3 行で条件記載なし・ソース冒頭に権利表記なし。**「無記載」であって「許諾」ではない**（[shared/02:812](../shared/02-hardware-design.md:812) の「個別確認まで不可」のまま） | Yahboom へ確認（要点は ④-7・送るのはオペレーター）。**不可なら ADR-0013 は reject へ**（Decision 2-(b)） |
| (c) ロールバック実証 | ツール候補 = `stm32flash`（Jetson の apt 候補 0.5・Mac は Homebrew）。ISP 突入は公式 wiki の手動（BOOT0 押下 → RESET → 離す）と mcuisp 自動（DTR = reset・RTS = BootLoader）の 2 経路（[ADR-0013:17](../adr/0013-stm32-command-stream-watchdog.md:17)） | 拡張ボード接続時に ④-5 を実施。CH340 の DTR/RTS が RESET/BOOT0 に配線されているかは回路図で確認（未確認） |
| (d) G-g | 未実施（§4・[mode-x-er/10:486](../mode-x-er/10-room-scale-safety-review.md:486)） | ④-6 の拡張行を含めて実施 |
| toolchain（`OQ-OD75`・**前提ゲート (c) に含めるか否かは未裁定**） | 配布物は Keil MDK（ARM Compiler 5）プロジェクトのみ・Mac / Jetson に Keil 無し（2026-09-12 追補 行 5） | ④-4 = **GCC 移植を採用案**として提示（裁定待ち） |

### ④-2. 追記の設計（hook 2 点・状態 3 語・停止 1 種・不変 4 点）

**流用する既存資産**: 純判定は ESP32 firmware track の [`firmware/include/command_watchdog.h:32`](../../firmware/include/command_watchdog.h)（`command_is_stale(last_ms, now_ms, timeout_ms)`・unsigned rollover-safe・境界「経過 == timeout は fresh」・R-26 host test = `firmware/test/test_watchdog/` + [`run_watchdog_test.sh`](../../firmware/test/run_watchdog_test.sh)・CI `firmware-safety`）と**同一意味論**にする。同ヘッダは C++（`<cstdint>`・`static_cast`）なので、STM32 側（C）からは (i) 同ヘッダを C/C++ 両対応化（`<stdint.h>` / `<stdbool.h>`・`static inline`。firmware track の変更）か (ii) C の双子ヘッダを ④-7 の置き場に置き同一テストで固定、のどちらか。**(i) を推奨**（純判定の単一ソース。[firmware/CLAUDE.md:32](../../firmware/CLAUDE.md) の produce 行に STM32 消費者を足す）。

| 要素 | 内容 | vendor 側の接点（zip 内 file:line） |
|---|---|---|
| hook A（受信刻印） | 通常パーサ `Upper_Data_Parse` の `case FUNC_MOTION:` 冒頭で `last_cmd_ms = xTaskGetTickCount(); armed = 1; tripped = 0;` | `Source/APP/protocol.c:352`（関数）・`:519`（case）。ゼロ指令（`:529-532` → `Motion_Stop(STOP_BRAKE)`）も刻印する＝停止中もホスト生存を数える |
| hook B（周期判定） | `vTask_Control` のパース直後・`SBUS_Handle()` の前に `if (armed && !tripped && command_is_stale(last_cmd_ms, now_ms, T_ms)) { Motion_Stop(STOP_BRAKE); tripped = 1; }` | `Source/APP/app.c:193-207`（1 ms ループ・prio 9・`:199` が `Upper_Data_Parse`）。hook A と**同一タスク**＝共有変数の競合なし。`xTaskGetTickCount()` は 1 tick = 1 ms（`FreeRTOS/inc/FreeRTOSConfig.h:47` `configTICK_RATE_HZ 1000`）。10 ms 側（`vTask_Speed` `app.c:146-158`・`Motion_Handle()` の隣）に置く代替もあるが、タスクを跨ぐ分だけ理由が要る |
| 停止種別 | `Motion_Stop(STOP_BRAKE)`（`Source/APP/app_motion.c:161-167`: 4 輪目標 0・`PID_Clear_Motor`・`g_start_ctrl = 0`・`Motor_Stop(brake)`）。W-1 / W-2 と同じ種別・冪等。1 episode に 1 回（`tripped`）で、次の `FUNC_MOTION` が re-arm する | SBUS 経路の「ブレーキ 100 ms 後に FREE へ切替」（`Source/APP/app_sbus.c:165-170`）は**踏襲しない**（歩道勾配で転がさない） |
| arm 条件 | UART からの初回 `FUNC_MOTION` で armed。armed 前は**何もしない**＝ホスト不在の stock 運用（SBUS RC・CAN）を壊さない | SBUS（`app_sbus.c`）・CAN（`Source/BSP/bsp_can.c:29` → `protocol.c:972` の別 `FUNC_MOTION`）・低電圧の縮小パーサ（`protocol.c:259`）は対象外。低電圧ラッチ時は `vTask_Speed` が既に `Motion_Stop(STOP_FREE)`（`app.c:160`）で、`vTask_Control` の主ループも抜ける |
| 不変 4 点 | メカナム IK・±700 二段 clamp（`app_motion.c:450-457` の M1 分岐）・シリアルプロトコル・auto-report（ADR Decision 1） | diff = vendor ファイル 2 箇所（`protocol.c` 1 行 + `app.c` 数行）＋ 新規 2 ファイル（判定 = 既存ヘッダ・状態 = 1 ファイル）。IWDG は射程外（Decision 4） |

### ④-3. T の決め方 — W-1 との層間整合（ADR Decision 3 の裁定案）

**ホスト送信の実体**（fresh の間に keepalive は無い）:

- `on_cmd_vel` は上流 `/bot1/cmd_vel` の到着ごとに clamp → 1 フレーム送信（[driver_core.py:267](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_core.py:267)）。
- `on_watchdog_tick`（既定 0.1 s = [driver_node.py:80](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_node.py:80)・`:142`・`:190`）は **`config_error is None ∧ fresh ∧ ¬overlay_blocks` の 3 条件が全て真のときだけ何も送らず `return False`**（[driver_core.py:329-331](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_core.py:329)・docstring [:310](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_core.py:310)「W-1 AND stop overlay」）。stale（最終受信から `cmd_vel_timeout_s` 超）・停止上乗せ成立・状態途絶・`config_error` のどれかなら毎 tick brake を送る。`cmd_vel_timeout_s` の runtime 実体は ROS param（[driver_node.py:77](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_node.py:77)・既定 0.5 s）で、[driver_core.py:56](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_core.py:56) の `DEFAULT_CMD_TIMEOUT_S` はその fallback 定数。
- 上流の周期: teleop_joy は固定 20 Hz 再送（[teleop_joy.py:93](../../ws/src/warehouse_teleop/warehouse_teleop/teleop_joy.py:93) `publish_rate_hz`・[m1_teleop.launch.py:102](../../ws/src/warehouse_teleop/launch/m1_teleop.launch.py:102) `autorepeat_rate` 20）・Nav2 controller 20 Hz（[nav2_params.yaml:80](../../ws/src/warehouse_bringup/config/nav2_params.yaml:80)。repo にある唯一の Nav2 params で `use_sim_time: true` の sim 設定＝実機の上流周期は Phase 1 で確定）。

**帰結**: ホストが生きていても、上流が沈黙した直後は **最大 `cmd_vel_timeout_s + watchdog_period_s` ≈ 0.6 s（既定）フレームが途切れる**。T をこれより短くすると W-3 が W-1 より先に発火し、W-1 の運用値（ROS param）は T 以下でしか意味を持たなくなる（[05:69](05-operation-state-and-stop-authority.md:69) が「守る故障が違う timeout を混ぜない」とした境界がここでも問われる）。

| 案 | ホスト変更 | T の下限 | 暫定値 | 層の発火順序 |
|---|---|---|---|---|
| α: T > W-1 の空白 | なし | `cmd_vel_timeout_s + watchdog_period_s` × 余裕 1.5 以上 | **1.0 s** | 上流沈黙 → W-1（0.5 s）が先。ホスト生存中に W-3 は発火しない。ホスト死 → W-3（1.0 s）。停止まで最長 1.0 s = 0.3 m/s で 0.3 m・屋外候補 1.31 m/s（150 mm・本 doc `:114` / `OQ-OD71` [07:206](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:206)。凍結契約は [safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:18) の 0.3 のまま）なら 1.3 m。**注意**: T は flash 定数、`cmd_vel_timeout_s` は上限なしの runtime param（`_positive_or_default` は非正・非有限だけ既定へ = [driver_core.py:99-101](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/driver_core.py:99)・[warehouse_m1_driver/CLAUDE.md:174](../../ws/src/warehouse_m1_driver/CLAUDE.md) と同じ信頼クラス）なので、`cmd_vel_timeout_s > T / 1.5` にすると発火順序が無言で反転する。α は運用規律でしか保てない（β を推奨する追加の根拠） |
| β: keepalive 追加 | **あり（L0'）**: `on_watchdog_tick` が**上記 3 条件（`config_error is None ∧ fresh ∧ ¬overlay_blocks`）の全て真の tick だけ**、直前に clamp 済みの wire 値を再送（既存の stale → brake と対称）。停止上乗せが塞ぐ tick・状態途絶・`config_error` の tick は**従来どおり brake**（brake フレームが keepalive を兼ね、hook A はゼロ指令も刻印する＝④-2）。[05:65](05-operation-state-and-stop-authority.md:65)-66 の契約表と [05:70](05-operation-state-and-stop-authority.md:70) R-26 ⑥「W-1 との AND 合成」は不変・clamp 必経も不変。R-26 unit は「再送値 = 直前の clamp 出力」に加え**「停止上乗せ成立中に motion 再送が起きない」negative oracle** を pin し、既存 [`tests/unit/test_m1_stop_overlay.py:99`](../../tests/unit/test_m1_stop_overlay.py) / `:261` を赤にしない | `3 × watchdog_period_s` | **0.3 s**（ADR の例示 0.3 s と一致） | ホスト生存中は常時 ≥10 Hz でフレームが届く → W-1 と W-3 は**守る故障で完全に分離**（上流沈黙 = W-1 のみ・ホスト死 = W-3 のみ）。停止まで最長 0.3 s |

- **推奨 = β**。W-1 / W-3 の層分離を保ったまま T を短くできる。FW 側の受け口は再送に対して冪等: 非ゼロ指令は `Motion_Ctrl` → `Motion_Set_Speed`（`app_motion.c:172-183`）→ `PID_Set_Motor_Target`（`Source/APP/app_pid.c:214-227`・`target_val` の代入のみ・積分項は触らない）、ゼロ指令は `Motion_Stop(STOP_BRAKE)`（停止中の再実行は無害）。
- **最終値は G-g の実測で確定**（ADR Decision 3。本表の 1.0 / 0.3 は**暫定**）。判定周期（hook B = 1 ms）は T に対して無視できる。

### ④-4. toolchain（`OQ-OD75` の裁定案）: GCC 移植を採用

| 事実（zip 実 Read） | 帰結 |
|---|---|
| `rosmaster.uvprojx`: `<Device>STM32F103RC</Device>`・`<ToolsetName>ARM-ADS</ToolsetName>`・`<uAC6>0</uAC6>`（ARM Compiler 5）。Makefile / CMake / CubeIDE プロジェクト無し | Keil MDK は Windows 専用・AC5 はレガシー。Mac / Jetson には無い |
| プリコンパイル `.lib` / `.a` **0 件**（ICM-20948 ライブラリもソース同梱・`<Define>MEMS_20648,...`） | GCC で全ソースを再コンパイルできる（バイナリ互換の壁が無い） |
| ARMCC 固有: `__asm` 4 file（`CMSIS/core_cm3.*`・`FreeRTOS/inc/portmacro.h`・`FreeRTOS/src/port.c`）・`__inline` 4 file・`#pragma pack(1)` 1 file（`Source/TOOL/tool_pid.h:68,79`）・`__weak` 1 file・startup は armasm 構文（`AREA` / `PRESERVE8`） | `Source/` はほぼコンパイラ中立。差替えは周辺 3 点: ① CMSIS core（CMSIS-Core の GCC 対応版）② FreeRTOS **V10.4.3 LTS Patch 1**（`FreeRTOS/inc/task.h:47`）の port を RVDS/ARM_CM3 → **GCC/ARM_CM3**（upstream 同梱）③ startup + リンカスクリプト（STM32F103RC = Flash 256 KB / SRAM 48 KB・ST の GCC テンプレート） |
| 書込は UART ISP（USART1 = PA9 / PA10 `Source/BSP/bsp_usart.c:72,78`） | ④-5 |

- **採用案**: `arm-none-eabi-gcc`（Jetson apt `gcc-arm-none-eabi` 10.3 / Mac Homebrew）で移植ビルド。**stock hex とのバイナリ同一性は放棄**する（コンパイラが違う）。「clamp・IK 不変」の証明は ADR Trade-offs のとおり**ソース diff（vendor 部分の変更が ④-2 の 2 箇所だけ）＋ G-g 回帰（④-6 行 7）**で行う。
- 非採用: Keil MDK Community Edition（無償・非商用）は Windows 専用で環境が無い。有償 MDK は fork 保守のためだけに購入しない。
- 移植の第一マイルストーン = **無変更ソースの GCC ビルド → 書込 → `m1_probe` で version / car_type / encoder が stock と同じに読める**。追記ゼロで「移植だけ」を先に検証し、追記の効果と移植の副作用を分離する。

### ④-5. ゲート (c) ロールバック手順（stm32flash・Mac / Jetson・W-4 適用）

前提: 車輪浮かせ・主電源に手（§4 手順 0）。シリアルは `/dev/myserial`（[jetson/02 §10](../jetson/02-remote-access-and-dev-link.md)）。

| # | 手順 | 判定・注意 |
|---|---|---|
| 1 | ISP 突入: 手動（BOOT0 押下 → RESET → BOOT0 離す）または `stm32flash -i` の DTR/RTS シーケンス（mcuisp 設定「DTR low = reset・RTS high = BootLoader」の写像）。**自動経路は CH340 の DTR/RTS が RESET/BOOT0 に配線されている場合だけ**（回路図で先に確認） | `stm32flash /dev/myserial` が応答し、Device ID **0x414**（STM32F10x High-density）・Flash 256 KiB と読めること |
| 2 | read-back: `stm32flash -r stock-readback.bin /dev/myserial` | **RDP（読出保護）が有効なら失敗する**。その場合 read-back は諦める（`-k` / `-u` の保護解除は**全消去**を伴うので使わない）。成功なら `rosmaster_V3.6.5.hex` を bin 化して比較 → 一致で (a) を patch レベルで閉じる |
| 3 | 書き戻し: `stm32flash -w rosmaster_V3.6.5.hex -v /dev/myserial` | verify 通過。golden image は [shared/02:798](../shared/02-hardware-design.md:798) |
| 4 | BOOT0 を離してリセット → `m1_probe`（read-only）で `get_version()` 3.6・`get_car_type_from_machine()`・電池電圧 | **car_type が 0x0A でなければ**（flash データ sector 120 = `Source/APP/app_flash.h:9,20` `F_CAR_TYPE_ADDR` が消去されると `Flash_CarType_Init` は `CAR_MECANUM` に戻す = `Source/APP/app_flash.c:88-96`）→ [ADR-0010 追補 :98](../adr/0010-raise-speed-cap-to-platform-max.md:98) の処置（`set_car_type(10)`）を再実行し、読み戻し 10 を確認 |
| 5 | 記録 | 使ったコマンド・Device ID・verify 結果・car_type を [shared/02](../shared/02-hardware-design.md) 末尾 P-8 系に追記（別 PR） |

これが通って初めて「カスタム hex を書いても戻れる」= Decision 2-(c)。カスタム hex の書込は、このあと同じ手順 3 を追記ビルドの hex で繰り返すだけ。

### ④-6. G-g 手順の拡張（§4 の表に足す行・追記後に実施）

| # | 手順 | 観察 → 判定 |
|---|---|---|
| 5 | 追記 FW で最小速度指令を常駐送信（β なら keepalive 込み）→ ホストプロセスを **`kill -9`**（W-2 が動かない死に方） | T + 判定周期以内に停止（車輪浮かせ）。停止までの時間を記録し、T を確定（ADR Decision 3） |
| 6 | 走行指令中に **USB を抜く**（§4 手順 2 の本命・追記後の再実施） | 同上。挿し直し後、次の `FUNC_MOTION` で再開すること（re-arm） |
| 7 | 回帰: ホストから上限超の指令を送る（既存 R-26 unit と同じ入力） | wire に上限超が出ない（L0'）**かつ** FW 側 ±700 clamp が生きている（`get_motor_encoder()` から求めた車輪速が 700 mm/s 相当を超えない）。IK 不変は前後・横・旋回の単独指令で 4 輪の回転方向が stock と同じこと |
| 8 | ホスト不在起動: 電源投入後にホストを繋がず 10 s 放置 | **何も起きない**（armed 前は無動作）。SBUS / CAN を使わない M1 ではこれで十分 |

合格の定義: 5・6 が T 以内に停止、7・8 が stock と同じ。**成功しても W-4 の緩和は自動ではない**（G-h・[mode-x-er/10:487](../mode-x-er/10-room-scale-safety-review.md:487)・ADR Decision 5）。結果は §3 の W-3 行・W-4 行と [mode-x-er/10:486](../mode-x-er/10-room-scale-safety-review.md:486) G-g 行・[shared/02:329](../shared/02-hardware-design.md:329) の TODO へ反映（別 PR）。

### ④-7. ライセンス確認（ゲート (b)）と追記ソースの置き場

- **問い合わせの要点（Yahboom へ・送るのはオペレーター）**: ① 対象 = `ROS-Driver-Board-FW-master.zip`（V3.6.5）② 行為 = 自分の 1 台に対し通信途絶で停止する watchdog を追記して書き込む ③ 再配布・公開・販売はしない・改変版は手元のみ ④ vendor のサポート対象外になることは了解 ⑤ 可否と、可なら条件（表示義務など）。英文 1 段落・日本語併記で足りる。
- **置き場（Decision 2 の「repo に置かない」・[shared/02:812](../shared/02-hardware-design.md:812) との整合）**: vendor ツリーは `~/Developer/mwr-vendor-code-cache-20260824/`（[shared/02:779](../shared/02-hardware-design.md:779)）配下に展開し、**ローカル git（push しない）**で stock を初期コミット・追記を 1 コミットにして差分を管理する。repo に置くのは**自作分だけ**: 純判定（④-2 の (i) なら既存 `firmware/include/command_watchdog.h`）・状態 1 ファイル・host test・GCC ビルド手順・「vendor 側 2 箇所に何を足すか」の**指示書**（vendor コードを含まない）。置き場は `firmware/` 配下の新サブディレクトリ（ESP32 の PlatformIO ツリーと混ぜない。名前は実装 PR で）。

### ④-8. 残件（隠さない）

1. ライセンス可否（(b)・最優先・外部依存）。
2. α / β と T の裁定（④-3）。β はホスト L0' の変更（3 条件 carve-out 付き keepalive 再送 + R-26 unit）を伴う。
3. CH340 DTR/RTS → RESET/BOOT0 の配線有無（回路図）・RDP の有無（ISP 応答で判明）。
4. `command_watchdog.h` の C 両対応化（(i) 採用時・firmware track）。
5. SBUS / CAN の有効状態（`ENABLE_SBUS` / `ENABLE_USART2` は `Source/APP/config.h` の `ENABLE_*` 群に定義が無く、`uvprojx` の Define も `MEMS_*` のみ）。M1 では未使用の前提で arm 条件を設計した。
6. toolchain 裁定（`OQ-OD75`）: ④-4 は**選択**（Keil vs GCC）の裁定案。**前提ゲート (c) に toolchain 調達を含めるか否か**（`OQ-OD75` の本題・本 doc 2026-09-12 追補 行 5）は別途裁定し、移植の第一マイルストーンも同時に決める。
7. G-g 実施後の docs 同期（④-6 末尾）。

- backlink: [ADR-0013 §References 末尾](../adr/0013-stm32-command-stream-watchdog.md) / [GLOSSARY §5 command-stream watchdog](../GLOSSARY.md) / [mode-outdoor/07:210 `OQ-OD75`](../mode-outdoor/07-drivetrain-and-wheel-sizing.md:210)（forward は land 済）/ [firmware/CLAUDE.md](../../firmware/CLAUDE.md)（`command_watchdog.h` produce 行）/ [mode-outdoor/05 §6](../mode-outdoor/05-safety-envelope-and-intervention.md:86)（fail-active 対策・W-3 とゲート (d) の優先度・forward 済）
