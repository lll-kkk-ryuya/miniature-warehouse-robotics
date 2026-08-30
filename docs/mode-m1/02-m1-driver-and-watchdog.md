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

- 上限値は `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY` を**単一ソース import**（[02:327](../shared/02-hardware-design.md)。現契約値 0.3 のまま = [ADR-0010:21](../adr/0010-raise-speed-cap-to-platform-max.md) の docs 先行原則）。
- **TF は出さない**（`odom→base_link` は ekf_node 単一所有 = [23:163](../architecture/23-perception-and-localization.md)。odom は topic publish のみ）。
- 採用禁止: `set_speed_limit(0x16)` / `set_imu_adjust(0x17)` は推測 API・実装なし（[02 V-1 :536](../shared/02-hardware-design.md)）。
- R-26: 「dispatch 経路が `clamp_body_velocity` を必ず通る」を独立オラクル unit + mutation で pin（= [mode-x-er/10:491 G-l](../mode-x-er/10-room-scale-safety-review.md) の条件 (ii)）。unit は `tests/unit/` に置く（CI 可視性 = [warehouse_m1_driver/CLAUDE.md](../../ws/src/warehouse_m1_driver/CLAUDE.md) の教訓）。

## 3. watchdog 多層停止設計

| 層 | 機構 | 守る故障 | 状態 |
|---|---|---|---|
| **W-1** | driver 内 **cmd_vel freshness timeout**: 上流からの最終受信から T 秒で自発的にゼロ送出（brake） | 上流（Nav2 / teleop / joy）の沈黙・ハング | **実装済（#550）**: T は ROS param 注入・既定 0.5s（`DEFAULT_CMD_TIMEOUT_S`＝[twist_mux.yaml:44](../../ws/src/warehouse_bringup/config/twist_mux.yaml)（凍結契約）整合・非正/非有限 param は既定へ fail-safe）。運用値の実機確定は `# TODO(Phase 1 実測)` 継続 |
| **W-2** | **atexit / SIGINT / SIGTERM handler で stop フレーム必送**: `0x12` ゼロ + `0x0F` の**二重送出** | driver の正常・準正常終了（Ctrl-C・例外死） | **実装済（#550）**: `M1DriverCore.shutdown_sequence`（冪等・一度だけ）＋ `driver_node` の atexit / SIGINT / SIGTERM 配線。R-26 unit 済（`tests/unit/test_m1_driver_core.py`） |
| **W-3** | MCU 側 communication watchdog | ホスト kernel 死・USB 断（W-1/W-2 が動けない故障） | **不在が濃厚（§1-2）= この層は埋められない**。G-g 実機確認（§4）で確定 |
| **W-4** | **運用**: 実施者がバッテリー主電源カットオフに手を掛けたまま実施・初回試験は車輪を完全に浮かせる | 全層失敗（W-3 不在の代替） | [mode-x-er/10:376 P-1](../mode-x-er/10-room-scale-safety-review.md)（物理停止手段の到達性）を**必須化**する根拠が §1-2 で確定 |

- W-1 と W-3 は**守る場所が違う**（W-1 = ホスト内・W-3 = ホスト死そのもの）。W-3 が埋められない以上、**W-4 は省略可能な備えではなく必須の層**である。
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
