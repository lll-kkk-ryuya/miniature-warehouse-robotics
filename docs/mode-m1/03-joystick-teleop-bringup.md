# joystick 手動走行 bring-up（成功の 3 段ゲート M0 / M1 / M2）

> **Status**: オペレーター指示 2026-08-26「物理的な起動はまず joystick ベースの手動走行を目標にする」の設計 doc。実装は land 済（driver = #550 `m1_driver` ／ joy 変換 = #551 `warehouse_teleop` ／ 操作者非常停止 latch・再アーム = #622 ／ 終了経路の Humble 対応 = #633）。**M0/M1/M2 ゲートはいずれも未クローズ**（M0 は給電テスターゲート①〜④のみ 2026-09-04 通過 = [02 P-9c :918](../shared/02-hardware-design.md)・負荷実測②〜④は未了。§2 実機プローブの実施記録は本 doc 未反映＝別 PR で追記）。**実機で叩くコマンドの正本（`joy_node` 起動 param・`jstest` によるモード判定と index 確定）は §5 追記**（2026-09-10）。
> **layer**: 経路は L4/L3/L2 を通らない bring-up 構成（joy → 変換 node → **L0'** driver）。安全は L0' クランプ + [02 §3](02-m1-driver-and-watchdog.md) watchdog 層 + W-4 運用が担う。

## 1. 成功の 3 段定義（混ぜない — 壊れ方が違う）

| ゲート | 成功条件（反証可能な形） | 落ちたときに疑う場所 |
|---|---|---|
| **M0 給電** | バッテリー直タップ → ヒューズ 10A → 昇圧 12.6→19V → Orin が安定起動し、走行負荷中もブラウンアウトしない | 電源設計・配線・昇圧設定（テスターゲート = [02:451](../shared/02-hardware-design.md) 手順①〜④厳守） |
| **M1 疎通** | Orin → 拡張ボード V3.0（CH340・115200）シリアル到達。`get_car_type()` / `get_motion_data()` が返り、**車輪を浮かせて**モータが回る | **一次: L4T に `ch341.ko` が無い → out-of-tree ビルド**（[jetson/02 §10 :411](../jetson/02-remote-access-and-dev-link.md)）→ **二層目: brltty が CH340 を横取り**（[jetson/02 §10.6 :499](../jetson/02-remote-access-and-dev-link.md)）→ **起動順序: T 挿し＋主スイッチ ON → USB**（[02 P-9d :932](../shared/02-hardware-design.md)）。udev `/dev/myserial` / permission / プロトコルはその後（2026-09-09〜10 実機で確定した順） |
| **M2 ROS 走行** | joystick → `/bot1/cmd_vel` → **m1_driver（`clamp_body_velocity` 必経）** → 実走。**かつ上限超指令を投げても wire に上限超が出ない**（negative test） | 実装（driver / 変換 node） |

- **M2 が本体**。negative test を入れることでデモではなく**ゲート**になる = [mode-x-er/10:491 G-l](../mode-x-er/10-room-scale-safety-review.md) のクローズと同時達成。
- 速度は**契約値 0.3 m/s のまま**行う（上限引き上げは car_type 実測後の contract PR = [ADR-0010:22](../adr/0010-raise-speed-cap-to-platform-max.md)。bring-up に速度の話を混ぜない）。

## 2. 実機プローブ（M1 ゲート内・5 分・[02 §4](02-m1-driver-and-watchdog.md) G-g と同一セッション）

| # | 測るもの | 決まるもの |
|---|---|---|
| 1 | `get_car_type()`（**最優先**） | ファーム clamp 上限（公式 V3.6.5 の M1 = `0x0A`→**0.7** m/s）= [ADR-0010 §Open 1](../adr/0010-raise-speed-cap-to-platform-max.md) の pin 値。**2026-09-10 実測: 工場値は `0x02`（X3 PLUS）だった → `0x0A` へ書換え済・電源断をまたぐ保持も確認**（#631・[ADR-0010 2026-09-10 追補](../adr/0010-raise-speed-cap-to-platform-max.md)・手順 [02 P-8-3](../shared/02-hardware-design.md)）。到達の前提は [jetson/02 §10](../jetson/02-remote-access-and-dev-link.md)（ch341・brltty・udev）と [02 P-9d](../shared/02-hardware-design.md)（起動順序） |
| 2 | モータラベルの RPM 印字 + ホイール径ノギス実測 | 理論最高速度・エンコーダ→距離換算（`ENCODER_CIRCLE_*` の選択 = [02 V-2 :543](../shared/02-hardware-design.md)） |
| 3 | トレッド / ホイールベース実測 → `(W+L)/2` | ファーム X3 幾何（`MECANUM_APB 164.555`）とのズレ量 → `wz` 補正係数の要否（[02 §1-3](02-m1-driver-and-watchdog.md)） |
| 4 | G-g watchdog 試験（[02 §4](02-m1-driver-and-watchdog.md)） | W-3 層の有無の確定 |
| 5 | `get_version()` | 実機ファーム版と調査ソース（**V3.6.5** = [02 P-7a :746](../shared/02-hardware-design.md)。旧 V3.5.1 GitHub mirror は履歴確認のみ = [02:584](../shared/02-hardware-design.md)）の一致（U-5）。**2026-09-10 実測 3.6**＝V3.6.5 系と major.minor で一致（vendor lib は patch を公開しない・#631） |
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

- receiver が標準 joystick デバイスであることの出典: Yahboom 公式 handle 制御ドキュメント（<http://www.yahboom.net/public/upload/upload-html/1684827990/5.%20Robot%20handle%20control.html> — 参照日 2026-08-26。`js0`・`jstest` 検証手順を明記）。実機の VID:PID / 直挿し vs HUB 経由は `lsusb` で確認（[shared/11 §6](../shared/11-m1-assembly-manual.md) の未決に答えを入れる）。 用語の正準は **ゲームパッド**（[GLOSSARY §11](../GLOSSARY.md)・別名 ハンドル/handle/joystick/joy＝同一物）。
- **Yahboom 公式の joy 変換 node（`yahboom_joy_*`）は不採用**（一次ソース実見・参照日 2026-08-26）:
  1. `/cmd_vel` へ直 publish = **L0' を素通り**する（[shared/11:128](../shared/11-m1-assembly-manual.md) の未決「handle 経路が L0' を通るか」への答え = **公式のままでは通らない**）。
  2. 既定 limit `xspeed 1.0 / yspeed 1.0 / angular 5.0` = 契約 0.3 を大幅超過。
  3. publish ゲートフラグが初期化後に更新されないバグ（非 root で無条件 publish）。
- **メカナム横移動（`vy ≠ 0`）を割り当てる場合はベクトルクランプ必須**（C-8 = [02:373](../shared/02-hardware-design.md)）。既存 `warehouse_teleop/keymap.py` のスカラー `clamp_velocity` は `vy` を扱えないため流用不可（オペレーター指示 2026-08-26「メカナムはベクトルで計算」と一致）。ただし**最終防衛は m1_driver 内の `clamp_body_velocity`**であり、変換 node 側のクランプは第一防御にすぎない。
- **デッドマンボタン**（押している間だけ publish）を必須にする（joy 特有の「スティック放置でゼロが流れ続ける」を利用して W-1 との整合も取る）。
- **mux の扱い**: Phase 1 bring-up は standalone 構成（Nav2 / twist_mux を立てない）で `/bot1/cmd_vel` を直接 publish → m1_driver が consume。これは既存 teleop の前提（[warehouse_teleop/CLAUDE.md:13](../../ws/src/warehouse_teleop/CLAUDE.md)）と同型。Nav2 同時稼働スライスで `/cmd_vel/teleop` の mux 入力追加が要る（bringup 所有 = 別調整・凍結 prio 100/10 には触れない = [twist_mux.yaml:42-48](../../ws/src/warehouse_bringup/config/twist_mux.yaml)）。
- **/joy 鮮度タイムアウト**（`joy_timeout_s`・既定 **0.6 s** = teleop_keyboard `stop_timeout` と同値）: Humble joy_node はデバイス喪失時に /joy の publish を止めるだけで**ゼロ Joy を出さない**（joystick_drivers ros2 branch `joy.cpp` handleJoyDeviceRemoved・一次ソース実見 参照日 2026-08-31）ため、変換 node は最後の /joy 受信から超過で republish をゼロ twist に落とす（デッドマン保持中に joy_node/receiver が死ぬと、保持指令が「新鮮な cmd_vel」として流れ続け [02 §3](02-m1-driver-and-watchdog.md) W-1 が発火しない穴を塞ぐ第一防御）。
- **操作者非常停止（latch）と再アーム**（正本 = [mode-m1/05 §5](05-operation-state-and-stop-authority.md)・[§6 の操作条件表](05-operation-state-and-stop-authority.md)）: **ゲームパッド**の空きボタンで 2 経路を同時に駆動する — **経路 A（standalone・唯一の停止経路）= `teleop_joy` 自身が latch 中 `/bot1/cmd_vel` へゼロを出し続ける**／**経路 B（統合構成）= `/operator/stop_request`（`std_msgs/String` JSON・契約 [doc03:112](../architecture/03-software-architecture.md)）へ rising edge で `{"action": "engage"}`・解除 chord で `{"action": "clear"}` を publish**（consumer = Emergency Guardian・param `publish_operator_stop` 既定 true で経路 B だけ切れる＝構成別 param = [05:92](05-operation-state-and-stop-authority.md)）。**既定 index は PC/PCS モード（軸 8/ボタン 15。LED 色では判定不可 = §5-2）の Yahboom 一次表**（<http://www.yahboom.net/public/upload/upload-html/1690197586/Handle%20control.html> 参照日 2026-09-09）に照合し、**実機握りテスト（2026-09-09）の結果で非常停止ボタンを 2 個に確定**: `estop_button` = **1（B・右親指＝狙って押す意図的停止）** ／ `estop_button_alt` = **7（R1・右人差し指＝驚いて握り込む反射停止）**——**どちらの rising edge でも engage**（意図的停止と反射停止は失敗モードが逆なので両方置く。alt は `-1` で無効化でき、機能そのものの有効/無効は primary `estop_button` だけで決まる＝alt は任意の追加）／`estop_clear_buttons` = **[10, 11]（SELECT+START 同時押し）** ／ `deadman_button` = **6（L1）に訂正**（旧既定 4 は **X-BOX モードの L1** であり PC/PCS では Y ボタンに当たるため。全て ros param 上書き可・実機は §2 の M1 ゲートで `jstest` 確定）。**解除 chord が効くのは、非常停止ボタン（primary・alt の両方）と deadman をすべて離した状態のときだけ**（握ったままの解除＝そのまま再発進、を作らない）。**再アームは 2 条件**: 解除・/joy 復帰の後、**① `axis_linear_x`/`axis_linear_y`/`axis_angular` の 3 軸すべてが一度中立になったことを観測**（中立判定は既存 `deadzone`（既定 0.1）を再利用し `isfinite(v) and abs(v) < deadzone`。**非有限・範囲外は中立と数えない**）→ **② deadman の押し直し（rising edge）**——押しっぱなし・スティックを倒したままでは再発進しない（[05:91](05-operation-state-and-stop-authority.md)）。**起動直後（ボタン履歴なし）は非対称**: deadman は**一度離して押し直す**まで arm しない（保持したまま起動しても走らない）が、**非常停止は起動時に握られていればそのまま engage する**（fail-safe＝停止側だけ履歴なしでも edge 扱い）。**sentinel は `estop_button = -1` のみ**（機能無効＝従来どおり deadman のみで走行可）。**`estop_clear_buttons = []` は sentinel ではない**——有効なのに解除手段が無い設定は**設定ミス**として扱う。**設定ミス（有効時の chord 空・範囲外 index・chord や deadman と非常停止ボタンの重複・`estop_button < -1`）は走行不許可に固定し、engage は publish しない**（typo で fleet 全体を止めないため）。**`joy_node` 側の前提 2 つ**: `autorepeat_rate` を **0 にしない**（既定 20 Hz。0 にすると押しっぱなしで /joy が止まり、上記 `joy_timeout_s` 0.6 s の鮮度ガードが正常操作を stale と誤判定する）・`sticky_buttons` は **false**（true はボタンをトグル化し、rising edge 検出と latch 意味論を壊す）。

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

## 5. 【2026-09-10 追記】実機手順の正本コマンド（`joy_node` 起動 param・`jstest` モード判定・`/joy` index 確定）

> §2 の M1 ゲート／§4 Phase E で**実際に叩くコマンド**の正本。`:52` は `joy_node` 側の前提（`autorepeat_rate` を 0 にしない・`sticky_buttons` は false）を**宣言している**が、**それを満たすコマンドも launch も docs / コードのどこにも無い**（[warehouse_teleop/CLAUDE.md](../../ws/src/warehouse_teleop/CLAUDE.md) 残件⑧「`autorepeat_rate` / `sticky_buttons` を強制する場所がコードにも launch にも無い」）。本節がその欠落を埋める。用語の正準は **ゲームパッド**（[GLOSSARY §11](../GLOSSARY.md)）。
> **本節は末尾 append**（`:52` は下流から多数 pin されるため、行数を変えない同一行置換〔LED 記述の訂正・§5-2〕以外は編集しない＝行数保存）。

### 5-1. `joy_node` の正本コマンド

```bash
# 受信機を挿した状態で（/dev/input/js0 の存在確認は 5-2 を先に行う）
ros2 run joy joy_node --ros-args \
  -p device_id:=0 \
  -p autorepeat_rate:=20.0 \
  -p sticky_buttons:=false \
  -p deadzone:=0.0
```

| param | 値 | なぜその値か（根拠） |
|---|---|---|
| `autorepeat_rate` | **20.0**（**0 にしない**） | 0 だと「押しっぱなしで /joy が止まる」ため、`teleop_joy` の `joy_timeout_s`（既定 **0.6 s** = `:51`）が**正常操作を stale と誤判定**してゼロ twist に落ちる。前提の宣言元は `:52` |
| `sticky_buttons` | **false** | true はボタンをトグル化し、**rising edge 検出と latch 意味論を壊す**。非常停止の engage・解除 chord・deadman 再アームは**すべて edge 依存**（[05 §5](05-operation-state-and-stop-authority.md) latch 意味論 / [05 §6](05-operation-state-and-stop-authority.md) 再アーム 2 条件）＝ここが false でないと停止権限が成立しない |
| `deadzone` | **0.0** | 中立判定の正本を `teleop_joy` 側 `deadzone`（既定 **0.1** = `joymap.DEFAULT_DEADZONE`。再アームの「3 軸中立」観測にも同じ値を使う = `:52` / `joymap._is_neutral`）**1 か所に寄せる**ため、`joy_node` 側は無効化する。`joy_node` の `deadzone` は外側の値を再スケールする実装が一般的で、0 以外にすると teleop 側 0.1 に当たる実効しきい値がずれる（upstream 実装の一次確認は `# TODO(実機・upstream 実装確認)`）＝0.0 ならその不確実性が効かない |
| `device_id` | 0 | 受信機 1 台だけを挿している前提（`device_id` は `joy_node` 側の列挙 index であり `jsN` の N と一致する保証は無い）。**複数のゲームパッドを挿したときの選択挙動は未検証** ＝ `# TODO(実機)` |

- **強制点は現状このコマンドだけ**。`joy_node` の param を固定する launch は**まだ無い**（#635 の code スライス＝未実装。`warehouse_teleop/CLAUDE.md` 残件⑧と同一）。launch が入るまでは、**上 2 つを手で渡し忘れた時点で非常停止 latch と鮮度ガードの前提が静かに崩れる**（症状が出るのは「押しっぱなしで止まる」「解除できない」という走行中の形）。
- M0-M2 bring-up は standalone（Nav2 / twist_mux を立てない = `:50`）なので、同時に上げるのは `joy_node` / `teleop_joy` / `m1_driver` の 3 つだけ。常駐（systemd）にはしない＝[01 常駐（systemd）の位置づけ](01-mode-boundary-and-traffic.md)。
- **3 プロセスの起動**（別端末 3 枚・この順）。param は既定のまま起動し、**上書きするのは 5-2 で `/joy` から実測した index だけ**:

    ```bash
    # ① joy_node = 上の正本コマンド
    # ② teleop_joy（既定 param。index の上書き例: --ros-args -p deadman_button:=6）
    ros2 run warehouse_teleop teleop_joy
    # ③ m1_driver = §2 プローブと G-g 抜線試験（[02 §4](02-m1-driver-and-watchdog.md)）を通した後・W-4（車輪を浮かせる）で
    ros2 run warehouse_m1_driver m1_driver
    ```

- **param が渡ったことの確認**（渡し忘れは症状が出るまで静かなので、起動直後に必ず見る）: `ros2 param get /joy_node autorepeat_rate` → `20.0`、`ros2 param get /joy_node sticky_buttons` → `False`、ボタンを押しっぱなしにして `ros2 topic hz /joy` → **≈20 Hz**（0 Hz なら `autorepeat_rate` が渡っていない）。

### 5-2. `jstest` でモード判定 → `/joy` でボタン index 確定（§2 M1 ゲート内で行う）

```bash
sudo apt install joystick   # jstest を含む。既に入っているか・要否は実機で確認 = # TODO(実機)
ls -l /dev/input/js0        # 受信機が標準 joystick デバイスとして見えているか（:31）
jstest /dev/input/js0
```

1. **冒頭に出る `Joystick (…) has 8 axes (…) and 15 buttons (…)` の行を読む**（逐語の書式は未実測＝`# TODO(実機)` で確定する）。
2. **判定規約（ここが正）** — モードは **button 数**で判定する:

| button 数 | モード | 扱い |
|---|---|---|
| **15** | **PC / PCS モード** | **正**。`:52` の既定 index（Yahboom 一次表由来）はこの並びを前提にしている |
| **11** | X-BOX モード | **切り替える**（ゲームパッド本体の **MODE 長押し**。button 数 11・長押し秒数 15 s は Yahboom「Handle control」一次表〔URL は `:52`・参照日 2026-09-09〕の読み取りで**実機未照合** = `# TODO(実機)`。実機では「15 と表示されるまで押す」を判定に使う）。この並びのままだと `deadman_button = 6` は L1 に当たらない（旧既定 4 の取り違えと同じ事故） |

3. ⚠️ **受信機 LED の色ではモードを判定できない** — **赤と緑が両方点灯する個体を 2026-09-09 に実機確認**した。したがって「受信機 LED 赤」は**モード判定の根拠として使えない**（`:52` の括弧書きは同一行置換で「LED 色では判定不可 = §5-2」に訂正済・行数不変）。判定は上表の button 数だけで行う。同じ「LED red」の括弧書きが `ws/src/warehouse_teleop/warehouse_teleop/joymap.py:42` のコメントにも残っている＝`warehouse_teleop` 所有のため、実測 index を既定へ反映する PR で同時に落とす（本 PR は docs のみ）。
4. **index の実測は `/joy` で行う**（`teleop_joy` の param は `/joy` の `buttons[]` / `axes[]` を添字する = `joymap.joy_to_twist` / `operator_estop_step`）。5-1 の `joy_node` を起動し、別端末で `ros2 topic echo /joy` を流しながら**ボタンを 1 つずつ押して `buttons:` 配列のどの位置が 1 になるかを読む**（`jstest` の `Buttons:` 表示は参考値＝手順 7）。期待値（`:52` の既定。**Yahboom 一次表からの導出で実測ではない**）:

| 役割 | ros param（`teleop_joy`） | 期待 index | 物理ボタン |
|---|---|:---:|---|
| deadman（押している間だけ走る） | `deadman_button` | **6** | L1 |
| 非常停止・意図的（狙って押す） | `estop_button` | **1** | B（右親指） |
| 非常停止・反射（驚いて握り込む） | `estop_button_alt` | **7** | R1（右人差し指） |
| latch 解除 chord | `estop_clear_buttons` | **[10, 11]** | SELECT + START 同時 |

5. 軸も同じ手順で確認する（`jstest` の `Axes:` 行）。既定は `axis_linear_x = 1` / `axis_linear_y = 0` / `axis_angular = 2`（`teleop_joy` の ros param・`joymap.DEFAULT_AXIS_*`）。
6. **期待値と違ったら、doc や既定値ではなく `teleop_joy` の ros param で上書きして M1 ゲートを続行する**（実測が正）。確定値の既定への反映（`:52` と `warehouse_teleop/CLAUDE.md`）は**別 PR**で行う。
7. **index の正は `/joy`**であって `jstest` ではない。`jstest` は joydev（`/dev/input/js0`）、Humble の `joy_node` は SDL 経由で evdev（`/dev/input/event*`）を読む（[jetson/02:352](../jetson/02-remote-access-and-dev-link.md) が `input` グループを要求する理由）ため、**両者の番号が一致する保証は無い**。`jstest` は「デバイスが見えているか」「モードが切り替わったか（button 数）」の確認に使い、**食い違ったら `/joy` が正**。一致の有無は M1 ゲートで両方を走らせて記録する = `# TODO(実機)`。

### 5-3. 参照（双方向）

- 本 doc 内: `:50`（standalone 構成）/ `:51`（`joy_timeout_s` 0.6 s の鮮度ガード）/ **`:52`（非常停止 latch・既定 index・sentinel・`joy_node` 前提の宣言元。本節の上書きは LED によるモード判定のみ）**/ §2（M1 ゲート）/ §4 Phase E。
- 停止権限の正本: [05-operation-state-and-stop-authority.md §5](05-operation-state-and-stop-authority.md)（latch 意味論・解除≠走行開始）/ [§6](05-operation-state-and-stop-authority.md)（操作条件表・再アーム 2 条件）。
- 実装側の produce/consume・残件: [ws/src/warehouse_teleop/CLAUDE.md](../../ws/src/warehouse_teleop/CLAUDE.md)（残件① `jstest` で index 未確定 / 残件⑧ 強制場所が無い）。
- 用語: [GLOSSARY §11 ゲームパッド](../GLOSSARY.md)。
- 常駐の扱い: [01-mode-boundary-and-traffic.md](01-mode-boundary-and-traffic.md)「常駐（systemd）の位置づけ」。
- Yahboom 一次ソース（「Handle control」・参照日 2026-09-09）の URL は `:52` に記載済み。本節は重複掲載しない。
