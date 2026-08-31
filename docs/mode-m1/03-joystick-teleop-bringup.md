# joystick 手動走行 bring-up（成功の 3 段ゲート M0 / M1 / M2）

> **Status**: オペレーター指示 2026-08-26「物理的な起動はまず joystick ベースの手動走行を目標にする」の設計 doc。実装は land 済（driver = #550 `m1_driver` ／ joy 変換 = #551 `warehouse_teleop`）。**M0/M1/M2 ゲートの実機実施は未実施**（§1 に実施記録なし・2026-08-30 時点）。
> **layer**: 経路は L4/L3/L2 を通らない bring-up 構成（joy → 変換 node → **L0'** driver）。安全は L0' クランプ + [02 §3](02-m1-driver-and-watchdog.md) watchdog 層 + W-4 運用が担う。

## 1. 成功の 3 段定義（混ぜない — 壊れ方が違う）

| ゲート | 成功条件（反証可能な形） | 落ちたときに疑う場所 |
|---|---|---|
| **M0 給電** | バッテリー直タップ → ヒューズ 10A → 昇圧 12.6→19V → Orin が安定起動し、走行負荷中もブラウンアウトしない | 電源設計・配線・昇圧設定（テスターゲート = [02:451](../shared/02-hardware-design.md) 手順①〜④厳守） |
| **M1 疎通** | Orin → 拡張ボード V3.0（CH340・115200）シリアル到達。`get_car_type()` / `get_motion_data()` が返り、**車輪を浮かせて**モータが回る | ケーブル・udev / permission・プロトコル |
| **M2 ROS 走行** | joystick → `/bot1/cmd_vel` → **m1_driver（`clamp_body_velocity` 必経）** → 実走。**かつ上限超指令を投げても wire に上限超が出ない**（negative test） | 実装（driver / 変換 node） |

- **M2 が本体**。negative test を入れることでデモではなく**ゲート**になる = [mode-x-er/10:491 G-l](../mode-x-er/10-room-scale-safety-review.md) のクローズと同時達成。
- 速度は**契約値 0.3 m/s のまま**行う（上限引き上げは car_type 実測後の contract PR = [ADR-0010:22](../adr/0010-raise-speed-cap-to-platform-max.md)。bring-up に速度の話を混ぜない）。

## 2. 実機プローブ（M1 ゲート内・5 分・[02 §4](02-m1-driver-and-watchdog.md) G-g と同一セッション）

| # | 測るもの | 決まるもの |
|---|---|---|
| 1 | `get_car_type()`（**最優先**） | ファーム clamp 上限（公式 V3.6.5 の M1 = `0x0A`→**0.7** m/s）= [ADR-0010 §Open 1](../adr/0010-raise-speed-cap-to-platform-max.md) の pin 値 |
| 2 | モータラベルの RPM 印字 + ホイール径ノギス実測 | 理論最高速度・エンコーダ→距離換算（`ENCODER_CIRCLE_*` の選択 = [02 V-2 :543](../shared/02-hardware-design.md)） |
| 3 | トレッド / ホイールベース実測 → `(W+L)/2` | ファーム X3 幾何（`MECANUM_APB 164.555`）とのズレ量 → `wz` 補正係数の要否（[02 §1-3](02-m1-driver-and-watchdog.md)） |
| 4 | G-g watchdog 試験（[02 §4](02-m1-driver-and-watchdog.md)） | W-3 層の有無の確定 |
| 5 | `get_version()` | 実機ファーム版と調査ソース（**V3.6.5** = [02 P-7a :746](../shared/02-hardware-design.md)。旧 V3.5.1 GitHub mirror は履歴確認のみ = [02:584](../shared/02-hardware-design.md)）の一致（U-5） |
| 6 | `get_motor_encoder()` が 4 値動くこと | 自前 odom（0x0D 経路）の前提 |

## 3. joy 経路設計

```
USB wireless handle receiver（/dev/input/js0・軸8・ボタン15・専用ドライバ不要）
      │
      ▼
joy_node（ROS 2 Humble 標準パッケージ・無改造）
      │ /joy
      ▼
自前変換 node（新規実装）── MAX_LINEAR_VELOCITY を import・デッドマンボタン
      │ /bot1/cmd_vel
      ▼
m1_driver（clamp_body_velocity 必経 = L0'）→ FUNC_MOTION 0x12 → STM32
```

- receiver が標準 joystick デバイスであることの出典: Yahboom 公式 handle 制御ドキュメント（<http://www.yahboom.net/public/upload/upload-html/1684827990/5.%20Robot%20handle%20control.html> — 参照日 2026-08-26。`js0`・`jstest` 検証手順を明記）。実機の VID:PID / 直挿し vs HUB 経由は `lsusb` で確認（[shared/11 §6](../shared/11-m1-assembly-manual.md) の未決に答えを入れる）。
- **Yahboom 公式の joy 変換 node（`yahboom_joy_*`）は不採用**（一次ソース実見・参照日 2026-08-26）:
  1. `/cmd_vel` へ直 publish = **L0' を素通り**する（[shared/11:128](../shared/11-m1-assembly-manual.md) の未決「handle 経路が L0' を通るか」への答え = **公式のままでは通らない**）。
  2. 既定 limit `xspeed 1.0 / yspeed 1.0 / angular 5.0` = 契約 0.3 を大幅超過。
  3. publish ゲートフラグが初期化後に更新されないバグ（非 root で無条件 publish）。
- **メカナム横移動（`vy ≠ 0`）を割り当てる場合はベクトルクランプ必須**（C-8 = [02:373](../shared/02-hardware-design.md)）。既存 `warehouse_teleop/keymap.py` のスカラー `clamp_velocity` は `vy` を扱えないため流用不可（オペレーター指示 2026-08-26「メカナムはベクトルで計算」と一致）。ただし**最終防衛は m1_driver 内の `clamp_body_velocity`**であり、変換 node 側のクランプは第一防御にすぎない。
- **デッドマンボタン**（押している間だけ publish）を必須にする（joy 特有の「スティック放置でゼロが流れ続ける」を利用して W-1 との整合も取る）。
- **mux の扱い**: Phase 1 bring-up は standalone 構成（Nav2 / twist_mux を立てない）で `/bot1/cmd_vel` を直接 publish → m1_driver が consume。これは既存 teleop の前提（[warehouse_teleop/CLAUDE.md:13](../../ws/src/warehouse_teleop/CLAUDE.md)）と同型。Nav2 同時稼働スライスで `/cmd_vel/teleop` の mux 入力追加が要る（bringup 所有 = 別調整・凍結 prio 100/10 には触れない = [twist_mux.yaml:42-48](../../ws/src/warehouse_bringup/config/twist_mux.yaml)）。

## 4. 物理手順の順序（正本への forward・本 doc は複製しない）

1. **Phase A（机上）**: Orin 単体ブート（QSPI 確認 → microSD → SSD 移行 = [02:407-412](../shared/02-hardware-design.md)。→ **SSD は B案＝`/ssd` データディスクで決着済・rootfs は microSD のまま** = [jetson/02:191-194](../jetson/02-remote-access-and-dev-link.md)）・マウント試し刷り（[02:414-420](../shared/02-hardware-design.md)。→ **[02 P-5](../shared/02-hardware-design.md) で fallback へ格下げ＝必須でない**）。
2. **Phase B（車体組立）**: [shared/11 §2-§3](../shared/11-m1-assembly-manual.md)（電源手順のみ §4 で差し替え）。
3. **Phase C（給電ハーネス）**: テスターゲート①〜④厳守（[02:451](../shared/02-hardware-design.md)。飛ばすと Orin 破壊）→ **M0**。
4. **Phase D（搭載・USB 配線）** → **Phase E（§2 プローブ + G-g）** → **M1**。
5. **Phase F（実装: m1_driver + joy 変換 node）** → negative test → **M2**。

## References

- [02-m1-driver-and-watchdog.md](02-m1-driver-and-watchdog.md)（L0' driver・watchdog 層・G-g 手順）
- [01-mode-boundary-and-traffic.md](01-mode-boundary-and-traffic.md)（bring-up 構成の traffic_mode 前提）
- [shared/02-hardware-design.md](../shared/02-hardware-design.md)（給電・フラッシュ経路・V-1〜V-5）/ [shared/11-m1-assembly-manual.md](../shared/11-m1-assembly-manual.md)（組立・§6 handle 未決）
- [jetson/01-fidelity-and-validation.md](../jetson/01-fidelity-and-validation.md)（G0-G7。M1 向け rescope は別 PR）
- [ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md)（S-SPEED は M2 達成後の別ゲート）
- [jetson/02-remote-access-and-dev-link.md](../jetson/02-remote-access-and-dev-link.md)（Phase A で確立した開発機↔Jetson のアクセス経路・初回ブート実測ベースライン）
