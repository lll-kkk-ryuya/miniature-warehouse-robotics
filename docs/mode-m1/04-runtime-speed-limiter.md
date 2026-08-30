# 04 — runtime speed limiter（走行中速度上限の動的変更・OQ-T3 設計解）

> **Status**: 設計 doc（docs 先行）。**実装は未着手＝本 doc に伴うコード変更はゼロ**。publisher node・Nav2 側配線・R-26 unit はすべて後続の実装スライス。
> **layer**（[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)・正準表 = [productization/01:180-188](../productization/01-commercial-box-map.md)）:
> - Nav2 `controller_server` / `nav2_params.yaml` = **L1 自律走行・安全**（[productization/01:185](../productization/01-commercial-box-map.md)）。本 doc が動かす主対象。
> - **L0'**（ホスト側シリアルドライバ送信直前クランプ・[mode-m1/02:4](02-m1-driver-and-watchdog.md)）= 不変。正準表への `warehouse_m1_driver`（L0'）行は **#555 で追記済**（[productization/01:187](../productization/01-commercial-box-map.md)・GLOSSARY §3 の正準エントリは #556）。
> - 帯の知覚（`gesture_detector`）= **L4 知覚・publish-only・0 actuation**（[mode-x-er/09:61](../mode-x-er/09-hand-raise-summon.md)）。
> - 新設する `speed_limit` publisher = **`gesture_detector` と同パッケージの別ノード**（[ADR-0012 決定 6](../adr/0012-speed-band-no-l2-best-effort.md) の条件付き裁定・2026-08-30。package 名は [09 OQ-13](../mode-x-er/09-hand-raise-summon.md) 従属・正準表への行追記は実装 PR の DoD）。control-plane であって velocity producer ではない（§4）。
> - L2 Traffic（`warehouse_traffic`）は Mode M1 では**非アクティブ**（`traffic_mode: none` = [mode-m1/01:14-16](01-mode-boundary-and-traffic.md)）。本 doc の安全論証を traffic 層に頼らない。

## 0. 位置づけ

[mode-x-er/09 §T-9 の **OQ-T3**（:436）](../mode-x-er/09-hand-raise-summon.md)——「**帯を走行中の Nav2 速度上限へ届ける ROS 経路**」が未設計である、という [T-6 :408](../mode-x-er/09-hand-raise-summon.md) の正直な限界——に対する**経路選定と制約照合を確定する doc** である。

本 doc が**する**こと: (a) 使う ROS 機構を Nav2 公式の `speed_limit_topic` / `nav2_msgs/SpeedLimit` に確定する（一次情報で裏取り = §2）／ (b) 起動時・runtime・物理限界の**三層モデル**を定義する（§2）／ (c) [09 T-6 の制約 1〜4（:410-413）](../mode-x-er/09-hand-raise-summon.md) との照合を 1 行ずつ行う（§4）／ (d) 一次情報の実読で判明した**罠**（`speed_limit=0.0` の意味・絶対値指定の fail-open 性）を明記する（§3）。

本 doc が**しない**こと: 帯の具体 m/s 値の決定（**S-SPEED 実測と contract pin 待ち** = [ADR-0010 §S-SPEED :31-39](../adr/0010-raise-speed-cap-to-platform-max.md) / [§Open 1-2 :63-64](../adr/0010-raise-speed-cap-to-platform-max.md)）・publisher node の実装・Nav2 params の実編集・新しい contract の凍結。

## 1. 問題の構造（CURRENT）— 速度上限は launch 時 1 回しか適用されない

現行の速度上限は**起動時の一方向注入**である（以下の行 pin は CURRENT 実体の記録＝churn 前提。恒久参照は §7 の契約形 anchor を用いる）:

```
config safety.max_linear_velocity（warehouse.base.yaml）
      │  load_config → _validate_safety（cap ≤ MAX_LINEAR_VELOCITY で fail-closed）
      ▼
_operating_vx_max()  … nav2_bringup.launch.py:258-270（min(cap, MAX_LINEAR_VELOCITY) で二重クランプ）
      │  launch arg max_linear_velocity の default（同 :329-340）
      │  PythonExpression min(float(x), MAX_LINEAR_VELOCITY)（同 :285-287。CLI override も上げられない）
      ▼
RewrittenYaml param_rewrites {"vx_max": vx_max}  … 同 :89-104
      ▼
MPPI FollowPath.vx_max  … nav2_params.yaml:122（在ファイル値 0.3 = 安全 default）
```

- この経路は **`generate_launch_description()` 評価時に 1 回だけ**走る。走行開始後に帯が変わっても、`vx_max` を書き換える手段は本 repo に無い。
- **`speed_limit` / `SpeedLimit` は本 repo の `ws/` `config/` に 1 件も存在しない**（2026-08-28 再 grep で確認。[09:408](../mode-x-er/09-hand-raise-summon.md) の 2026-08-21 grep 結果は今も有効）。
- 混同注意: Rosmaster ファームの **`set_speed_limit(0x16)` は別物であり採用禁止**（推測 API・実装なし = [mode-m1/02:55](02-m1-driver-and-watchdog.md)。※同行が併記する shared/02 旧 V-1 の記述は #559 系の V-1 改訂で削除済＝**出典失効**・裁定自体は 02:55 が保持）。本 doc の `speed_limit` は **Nav2 の topic 名**であって、このシリアルコマンドとは無関係。

## 2. 三層モデル（本 doc の核心）

速度上限を**役割の異なる 3 層**に分ける。どれか 1 つが他を代替することはない。

| 層 | 実体 | 適用時刻 | 役割 | 本 doc での扱い |
|---|---|---|---|---|
| **① 起動基準値** | config → `_operating_vx_max()` → `RewrittenYaml` → MPPI `vx_max`（§1） | 起動時 1 回 | **基準上限**。`config.py:101-105` の fail-closed 検証を通る唯一の入口。MPPI 内部では `base_constraints.vx_max` としてラッチされ、②のスケール基準になる | **廃止しない・不変**。②を足しても①は残す |
| **② runtime 帯上限** | `speed_limit`（`nav2_msgs/SpeedLimit`）→ `controller_server` → 各 controller plugin の `setSpeedLimit()` → MPPI `Optimizer::setSpeedLimit` | 走行中・任意 | **運用の動的上限**。帯（最遅段/最速段/安定段）を走行中に反映する | **本 doc で新規に確定する層** |
| **③ L0' 最終クランプ** | `clamp_body_velocity(vx, vy, wz)`（方向保存ベクトルクランプ）= [mode-m1/02:45](02-m1-driver-and-watchdog.md) | 送信直前・全指令 | **安全限界（safety envelope）**。上位が何を言おうと wire に上限超が出ない | **不変・廃止禁止**。②の下流に置く（②を L0' の下流に置かない） |

> **役割分担の一文**: ①＝**起動時の基準値**、②＝**運用の動的上限**、③＝**安全限界**。②は①を上書きするのではなく①を基準にスケールし、③は①②の正しさに依存しない最終防衛である。

### 2-1. ② の一次情報（Nav2 公式・すべて **humble ブランチ**で確認）

M1 の distro は **Humble**（[ADR-0008 §Decision :16](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）なので、裏取りは `humble` ブランチと Humble リリースで行った。参照日はすべて **2026-08-28**。

1. **`controller_server` の `speed_limit_topic` パラメータ（既定 `"speed_limit"`）が存在する。**
   - source（一次）: `declare_parameter("speed_limit_topic", rclcpp::ParameterValue("speed_limit"));`
     <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_controller/src/controller_server.cpp>
   - 公式 docs の説明文（型 `string` / 既定 `"speed_limit"` / 「Speed limiting topic name to subscribe. This could be published by Speed Filter … **You can also use this without the Speed Filter as well if you provide an external server to publish these messages.**」）は **docs.nav2.org リポジトリの `sphinx_docs` ブランチ（Humble 期の原文・一次）**で確認: <https://raw.githubusercontent.com/ros-navigation/docs.nav2.org/sphinx_docs/configuration/packages/configuring-controller-server.rst>（旧 `docs.nav2.org` の HTML は mkdocs 移行で 404。2026-08-30 に一次確認し、初稿のミラー確認 <https://ros.ncnynl.com/en/nav2/configuration/packages/configuring-controller-server.html> は一次ソースに置換済）。
   - **帰結**: Speed Filter（costmap filter + マスク画像）は**不要**。外部ノードが `speed_limit` を publish するだけで公式に成立する。**⚠️ トピック名は相対名（2026-08-30 訂正）**: 本 repo の `controller_server` は `/bot{n}` namespace 配下・relative topic 運用（[nav2_bringup.launch.py:11-13](../../ws/src/warehouse_bringup/launch/nav2_bringup.launch.py)）のため、解決後の実名は **`/bot{n}/speed_limit`**（bot1 なら `/bot1/speed_limit`）。絶対名 `/speed_limit` へ publish しても届かない。publisher は同 namespace で相対名を使うこと。
2. **`nav2_msgs/msg/SpeedLimit` のフィールド**（humble・全文）:
   ```
   std_msgs/Header header
   # Setting speed limit in percentage if true or in absolute values in false case
   bool percentage
   # Maximum allowed speed (in percent of maximum robot speed or in m/s depending
   # on "percentage" value). When no-limit it is set to 0.0
   float64 speed_limit
   ```
   <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_msgs/msg/SpeedLimit.msg>
   **`speed_limit = 0.0` は「停止」ではなく「制限なし（no-limit）」**——これが §3 の罠。
3. **`nav2_core::Controller::setSpeedLimit()` は公式 IF の純粋仮想**であり、`controller_server` が `speed_limit_topic` を購読して**全 controller plugin へ配る**:
   - `virtual void setSpeedLimit(const double & speed_limit, const bool & percentage) = 0;`
     <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_core/include/nav2_core/controller.hpp>
   - 購読とファンアウト（同 `controller_server.cpp`）:
     ```cpp
     speed_limit_sub_ = create_subscription<nav2_msgs::msg::SpeedLimit>(
       speed_limit_topic, rclcpp::QoS(10),
       std::bind(&ControllerServer::speedLimitCallback, this, std::placeholders::_1));
     // callback:
     for (it = controllers_.begin(); it != controllers_.end(); ++it) {
       it->second->setSpeedLimit(msg->speed_limit, msg->percentage);
     }
     ```
   - ⚠️ **QoS は `rclcpp::QoS(10)`（transient_local ではない）**＝**latch しない**。publisher が `controller_server` より先に 1 回だけ出した値は届かない → publisher は「帯遷移時の送出」に加えて**周期送出または lifecycle 起動後の再送**が要る（設計要件・§6 OQ-R4 に含める）。
   - ⚠️ 配布先は **`controller_server` が保持する controller plugin 群のみ**。`behavior_server`（recovery 挙動。本 repo では controller_server と**同じ** `cmd_vel/nav2` へ合流 = [twist_mux.yaml:16-19](../../ws/src/warehouse_bringup/config/twist_mux.yaml)「Nav2 controller_server AND behavior_server (recoveries) -> /bot{n}/cmd_vel/nav2」）には**効かないことをソースで確認済（2026-08-30 追補）**: `nav2_core::Behavior` IF（<https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_core/include/nav2_core/behavior.hpp>）は configure/cleanup/activate/deactivate のみで **`setSpeedLimit` を持たず**、`behavior_server.cpp` にも speed limit 購読は無い（recovery 速度は各 plugin 固有 param 例 `max_rotational_vel`）。**recovery 中の速度は②で構造的に縛れない**（③ L0' が担保する）。`nav2_velocity_smoother` にも `speed_limit` 購読は無い。
4. **⚠️ MPPI の `Optimizer::reset()` は適用済みの②を黙って破棄する（2026-08-30 追補・第3の罠）。** `reset()` は `settings_.constraints = settings_.base_constraints;` で①へ戻す＝**②が消える**。到達経路は 3 本（いずれも humble ソースで確認）: (i) **無活動タイムアウト** — `computeVelocityCommands` 冒頭で `now - last_time_called_ > reset_period_`（既定 **1.0s**・**Humble 固有 param**）なら `reset()`。goal 間に 1 秒以上空くのは通常運転＝**新しい goal のたびに帯上限が①へ戻る**。(ii) `Optimizer::fallback()`（soft-reset 失敗時）＝**走行中でも消える**。(iii) 動的 param 変更の post-callback。消える向きが**速い①への復帰**＝危険側。この事実により **周期 re-publish は「cadence の好み」ではなく安全要件**であり、周期は `reset_period` より十分短いこと（または `nav2_params.yaml` で `reset_period` を併せて明示設定）が必須 → **OQ-R4 に反映**。
5. **MPPI（`nav2_mppi_controller`）は `setSpeedLimit` を実装し constraints を更新する**。かつ **Humble にリリースがある**:
   - `nav2_mppi_controller/src/controller.cpp` → `optimizer_.setSpeedLimit(speed_limit, percentage);`
     <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_mppi_controller/src/controller.cpp>
   - `nav2_mppi_controller/src/optimizer.cpp`（**重要・§3 の根拠**）:
     ```cpp
     void Optimizer::setSpeedLimit(double speed_limit, bool percentage)
     {
       auto & s = settings_;
       if (speed_limit == nav2_costmap_2d::NO_SPEED_LIMIT) {
         s.constraints.vx_max = s.base_constraints.vx_max;   // …vx_min / vy / wz も base へ戻す
       } else {
         if (percentage) {
           double ratio = speed_limit / 100.0;
           s.constraints.vx_max = s.base_constraints.vx_max * ratio;   // …vx_min / vy / wz も同率
         } else {
           double ratio = speed_limit / s.base_constraints.vx_max;
           s.constraints.vx_max = s.base_constraints.vx_max * ratio;   // = speed_limit（上限クランプ無し）
         }
       }
     }
     ```
     <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_mppi_controller/src/optimizer.cpp>
   - `NO_SPEED_LIMIT` の実体: `static constexpr double NO_SPEED_LIMIT = 0.0;`
     <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_costmap_2d/include/nav2_costmap_2d/costmap_filters/filter_values.hpp>
   - Humble リリース: **`nav2_mppi_controller` 1.1.20 (Humble)** — <https://index.ros.org/p/nav2_mppi_controller/>（Humble 1.1.20 / Jazzy 1.3.13 / Kilted 1.4.2 / Lyrical 1.5.1）。
     → **[ADR-0008:25](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)「Nav2 の MPPI は Humble にもリリースがある（`nav2_mppi_controller` 1.1.20 for Humble）」と完全一致**（矛盾なし。Jazzy 側の patch 番号だけ 1.3.12 → 1.3.13 に進んでいる）。

### 2-2. ② が①を上書きする形（source から導いた事実・設計に効く）

- MPPI の `base_constraints` は params から読まれる値＝**①が注入した `vx_max`**。②は常に**①を基準にスケール**する。
- `percentage = true` も**上限クランプは無い**（`ratio = speed_limit / 100.0` をそのまま乗算＝`speed_limit=150` なら `ratio=1.5` で①を超える。2026-08-30 ソース再確認）。「①を超えない」性質は **publisher 側クランプ（OQ-R3）が担う**のであって % モード自体の性質ではない——ただし `speed_limit ≤ 100` を publisher が保証する限り `base × ratio ≤ base` は成立する。
- `percentage = false`（絶対値 m/s）は `ratio = speed_limit / base_vx_max` → `constraints.vx_max = speed_limit` **そのもの**。**上限クランプが無い**＝`speed_limit` に①より大きい値を publish すれば **`vx_max` は①を超えて上がる**（fail-open）。
- 副作用: どちらの分岐でも **`vx_min` / `vy` / `wz` が同率でスケール**する。絶対値 m/s を「linear だけの制限」と読むと誤り——**角速度 `wz` も同時に縮む/伸びる**。[ADR-0010 §Open 6 :68](../adr/0010-raise-speed-cap-to-platform-max.md)（wz 上限は新設しない）とは矛盾しないが（凍結契約に wz は無い）、挙動として明記しておく。

## 3. 帯 → `SpeedLimit` 写像

### 3-1. 形

- **`percentage = false`（absolute・m/s）を既定とする。** 理由: 契約上限（`safety.py:18`）・config 運用値（`safety.max_linear_velocity`）・[ADR-0010](../adr/0010-raise-speed-cap-to-platform-max.md) の S-SPEED 実測値が**すべて m/s** であり、同一単位で語れることが誤解を減らす。ただし §2-2 の fail-open 性ゆえ **publisher 側クランプが必須**（§4 制約 1）。`percentage = true` は「構造的に①を超えられない」利点があるため**捨てない** → **OQ-R5**。
- 帯は [09 T-1（:352-）](../mode-x-er/09-hand-raise-summon.md) の **3 帯**（**0本（グー）＝最遅段 / 1〜3本＝最速段 / 4〜5本（パー）＝安定段**）。
- **帯の実値（m/s）は本 doc で発明しない。** `config` 注入（**コード定数禁止** = [ADR-0010 §Decision 2 :23](../adr/0010-raise-speed-cap-to-platform-max.md) / [09 T-6 :406](../mode-x-er/09-hand-raise-summon.md) と同規律）。最速段の値は [ADR-0010 §Open 1-2 :63-64](../adr/0010-raise-speed-cap-to-platform-max.md)（car_type 実機確認 → contract pin）と **S-SPEED 実測**の後にしか決まらない（= [09 OQ-T1 :434](../mode-x-er/09-hand-raise-summon.md)）。config キー名も**凍結契約ではない**（additive / safe-OFF・既定 OFF = [09 T-6 :406](../mode-x-er/09-hand-raise-summon.md)）。

| 帯（[09 T-1](../mode-x-er/09-hand-raise-summon.md)） | `percentage` | `speed_limit` | 出所 |
|---|---|---|---|
| 最遅段（0本・グー） | `false` | config 注入値（m/s・**> 0**） | 未確定 = [09 OQ-T2 :435](../mode-x-er/09-hand-raise-summon.md) |
| 最速段（1〜3本） | `false` | config 運用値（= ①と同値になりうる） | 未確定 = [09 OQ-T1 :434](../mode-x-er/09-hand-raise-summon.md) / S-SPEED |
| 安定段（4〜5本・パー） | `false` | config 注入値（m/s・**> 0**） | 未確定 = [09 OQ-T2 :435](../mode-x-er/09-hand-raise-summon.md) |

### 3-2. ⚠️ `speed_limit = 0.0` の罠（fail-safe 設計の反転）

**`0.0` は「停止」ではなく「制限なし」**（§2-1 ②の msg コメント / `NO_SPEED_LIMIT = 0.0` / `Optimizer::setSpeedLimit` の第一分岐が `base_constraints` へ**戻す**）。したがって:

- **未検出・異常・初期化未了・エラー時に `0.0` を publish してはならない。** 直感どおりに「安全側＝0」と書くと、**帯上限が丸ごと外れて①の基準値まで戻る**（＝最も速い状態になりうる）。これは fail-safe のつもりで fail-open を書く典型であり、本 doc で最も強調すべき点である。
- publisher は **常に正の m/s を送る**（`> 0` を型/実装レベルで保証する）。`0.0` を「意図的に制限解除する」以外の用途で使わない。

### 3-3. 未検出時の挙動（doc09 T-5 との整合・**確定は実装スライス**）

[09 T-5（:388-397）](../mode-x-er/09-hand-raise-summon.md) は「保持窓内はホールド → **タイムアウト後は安定段へ復帰**（停止ではない）」と確定済み。これを ROS 経路へ写すとき、成立する形が複数ある:

| 案 | 形 | 評価 |
|---|---|---|
| (a) **安定段値を能動 publish** | タイムアウト時に安定段の m/s を送る | T-5 の意味論と 1:1。②の状態が publisher 側の意図と常に一致する。**推し**（ただし確定は実装スライス） |
| (b) publish 停止（最終値ラッチ依存） | 送るのをやめる。Nav2 は最後の値を保持 | 「タイムアウト後は安定段」を**満たさない**うえ、**依存する「最終値ラッチ」自体が永続しない**（§2-1 ④: MPPI `reset()` が②を破棄・無活動 1.0s で発火）。**T-5 と矛盾し、かつ前提が崩れているので不可** |
| (c) `0.0` を送る | — | **禁止**（§3-2。制限解除になる） |

- 補足: `controller_server` 側に `speed_limit` の失効（timeout）機構は無い（`speedLimitCallback` は配るだけ）が、**MPPI 側には §2-1 ④ の `reset()` 経路があり、失効は「安全側へ」ではなく「①＝速い側へ」起きる**（2026-08-30 追補・[部分裏取り]を解消）。したがって「送らない＝安全側に戻る」は**二重に成立しない**前提で設計する。
- **確定は実装スライス** = **OQ-R4**。本 doc は選択肢と禁止事項までを固定する。

## 4. [09 T-6 の制約 1〜4（:410-413）](../mode-x-er/09-hand-raise-summon.md) との照合

制約は doc09 から実 Read で転記した（原文の順序・意味を変えない）。

| # | 制約（[09:410-413](../mode-x-er/09-hand-raise-summon.md) 原文） | 本経路がどう満たすか |
|---|---|---|
| **1** | 「`config.py:101-105` の fail-closed 検証（≤ 契約上限）を**迂回しない**」 | ①は従来どおり `load_config` → `_validate_safety`（[config.py:101-105](../../ws/src/warehouse_interfaces/warehouse_interfaces/config.py)）を通る。②は Nav2 内部で `base_constraints` を**スケール**する経路であり、§2-2 のとおり `percentage=false` では **①を超えうる（fail-open）** → **publisher 側で `min(帯値, ①, MAX_LINEAR_VELOCITY)` の上限クランプを必須化**する（①＝launch が MPPI へ注入する解決値を同一 launch から publisher param で受ける。天井を config 運用値とする当初形は launch の LOWER-only override 構成〔`max_linear_velocity:=0.1` 等〕で①より高い帯が通るため **2026-08-30 改訂**＝[ADR-0012 決定 3](../adr/0012-speed-band-no-l2-best-effort.md)）。これは検証の迂回ではなく**同じ不変条件の二重化**であり、R-26 で pin する（OQ-R3） |
| **2** | 「twist_mux の 2 入力を**増やさない**」 | `speed_limit` は **control-plane（制約の宣言）**であって `cmd_vel` 系の velocity producer ではない。[twist_mux.yaml:41-49](../../ws/src/warehouse_bringup/config/twist_mux.yaml) の `emergency`(prio100) / `nav2`(prio10) の 2 入力は**不変**、[同 :5-8](../../ws/src/warehouse_bringup/config/twist_mux.yaml) の FROZEN safety contract（prio100 override 意味論）にも触れない。**3 本目の velocity producer を作らない** |
| **3** | 「**L0' クランプの下流に置かない**（クランプは常に最後）」 | ②の効果は Nav2 controller が出す `cmd_vel` の中身に現れる＝**L0' の上流**。L0'（[mode-m1/02:45](02-m1-driver-and-watchdog.md) `clamp_body_velocity` 必経）は②の有無に関係なく最終段に残る。②が壊れても③が wire 上限を守る（[mode-m1/03:12](03-joystick-teleop-bringup.md) の M2 negative test と同じ担保） |
| **4** | 「新しい actuation 経路を作らない（INV-2 ＝ T-8）」 | §4-1 で個別に論じる（**単純な YES とは書かない**） |

### 4-1. 制約 4（INV-2）の精査 — 文面ベースで論じ、曖昧さは OQ に落とす

[INV-2 の原文（09:57）](../mode-x-er/09-hand-raise-summon.md) は「gesture 経路は ER 呼び出しだけを省略し、**`to_robotics_plan_draft` 以降は 1 ステップも迂回しない**」であり、列挙されたゲート列は `handoff` 禁止キー gate → L3 Validator → Visual Resolver → Task Graph Executor → Command Compiler → action_map → MCP → **L2 Policy Gate** → Nav2 Bridge REST → Nav2 → L1 → L0'。
[T-8（09:424）](../mode-x-er/09-hand-raise-summon.md) はこれを受けて「速度帯は **Nav2 のパラメータ**であって新しい task / command / actuation 経路ではない」と判定している。

- **文面上の整合**: `speed_limit` は `Command` を生まず、目的地（どこへ行くか）を一切決めない。L2 Policy Gate の許可判断（location / freshness / battery / emergency / rate / duplicate）を**代替も迂回もしない**——判断対象そのものが存在しないため。この点で T-8 の「Nav2 のパラメータ」という性格づけは**そのまま生きる**。
- **ただし曖昧さが残る**: INV-2 の文面は「`to_robotics_plan_draft` **以降**のゲート列」を対象にしており、**plan draft を経ない control-plane 信号**を想定していない。②は「actuation を新設しない」が「**ゲート列の外側から走行の物理量に影響を与える**」という新種であり、これが INV-2 の精神（＝ゲート列を回避した影響経路を作らない）に触れるか否かは**文面だけでは決まらない**。とくに**上限の引き上げ（安定段 → 最速段 = loosen）**は「より危険な状態への遷移」であり、L2 が restrict-only（[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md)）で扱ってきた向きと逆である。
- したがって本 doc は**断定しない**。この裁定を **OQ-R1**（引き上げを L2 経由にすべきか）として明示し、[09:426](../mode-x-er/09-hand-raise-summon.md) の要求どおり **R-26 相当 unit で「L2 迂回が起きない」を pin する**設計（OQ-R3）を実装スライスの DoD に置く。
- 参考: [ADR-0010 §Decision 5 :26-29](../adr/0010-raise-speed-cap-to-platform-max.md) は速度上限の引き上げに **L2 鮮度窓の物理論証の再導出**と **L1 停止円 margin（`margin = v_max × t_react`）の再導出**を義務づけている。②で**走行中に**上限が変わるということは、**その瞬間に L2/L1 の前提が変わる**ということ——OQ-T3 が「ADR-0010 §Decision 5 の派生再導出と同一の安全レビューに掛ける」と書いている（[09:436](../mode-x-er/09-hand-raise-summon.md)）のはこの意味である。**本 doc は経路を選定しただけで、この安全レビューを済ませてはいない。**

## 5. teleop（joystick）には効かない — そこに穴が無いことの明示

**`speed_limit` は Nav2 の `controller_server` にしか効かない**（§2-1 ③のファンアウト先は controller plugin 群）。joystick 手動走行（[mode-m1/03 §3](03-joystick-teleop-bringup.md)）は Nav2 を経由しない（`joy_node` → 自前変換 node → `/bot1/cmd_vel` → m1_driver = [mode-m1/03:30-41](03-joystick-teleop-bringup.md)）ため、②はこの経路に**一切影響しない**。

穴が無い理由:

- teleop 側の第一防御は変換 node の **C-8 ベクトルキャップ**（メカナム `vy ≠ 0` を扱うためスカラー clamp は流用不可 = [mode-m1/03:48](03-joystick-teleop-bringup.md) / [shared/02:373](../shared/02-hardware-design.md)）。
- 最終防衛は**共通の L0'**（`clamp_body_velocity` 必経 = [mode-m1/02:45](02-m1-driver-and-watchdog.md)）。Nav2 経路と teleop 経路は**同じ ③ に合流する**。
- したがって「Nav2 側だけ帯で絞られ、teleop 側が野放しになる」構図は生じない。**ただし逆も真**——帯を**遅く**しても teleop の手動速度は遅くならない（teleop に帯は適用されない）。デモ運用でこの非対称を前提にすること。
- Phase 1 bring-up は standalone（Nav2 / twist_mux を立てない）構成であり（[mode-m1/03:50](03-joystick-teleop-bringup.md)）、その構成では②は**そもそも存在しない**。

## 6. 未決事項（新規 OQ・接頭辞 `OQ-R*`）

> **採番 scoping**: 接頭辞 `OQ-R*`（**R**untime speed limiter）は本 doc 固有。採用前に `grep -rn "OQ-R" docs/` で **0 件**（2026-08-28 確認）＝衝突なし。[09 の `OQ-T*`](../mode-x-er/09-hand-raise-summon.md) / [11 の `OQ-H*`](../mode-x-er/11-standby-and-hri-features.md) と同じ回避策。／**【2026-08-30】OQ-R1〜R7 は [ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md) で一括裁定済**（各行に行内追記。残る未決は OQ-T1/OQ-T2/OQ-13/`V_FLOOR`/過渡実測回帰＝ADR-0012 §Open）。

| # | 問い | 優先度 |
|---|---|---|
| **OQ-R1** | **帯の引き上げ（安定段 → 最速段 = loosen）を L2 Policy Gate 経由にすべきか。** 引き下げ（tighten）は無条件で可としてよい（より安全な向き・[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) の restrict-only と同じ向き）。引き上げは「より危険な状態への遷移」であり、[ADR-0010 §Decision 5 :26-29](../adr/0010-raise-speed-cap-to-platform-max.md) の三段（L2 鮮度窓 / L1 停止円 margin / 期待値の cap 相対化）と整合する裁定が要る。**本丸** → **裁定済（2026-08-30・[ADR-0012 決定 1](../adr/0012-speed-band-no-l2-best-effort.md)）: L2 非経由**。帯＝承認済み envelope 内の best-effort 制御面（安全機構ではない）。ADR-0010 §Decision 5 の三段は contract PR の DoD に紐づく義務で本件に及ばず、[09:436](../mode-x-er/09-hand-raise-summon.md) の安全レビュー相当は ADR-0012 §Trade-offs で消化 | **最高** |
| **OQ-R2** | **publisher node の パッケージ / layer 帰属。** 「control-plane であって velocity producer ではない」ことだけが確定。候補（`warehouse_m1_driver` は L0' 純度を汚すので不適・帯の出所は L4 `gesture_detector`・適用先は L1）と、[productization/01:180-188](../productization/01-commercial-box-map.md) 対応表への 1 行追記を同一 PR で行う義務（[layer-annotation.md](../../.claude/rules/layer-annotation.md)）。**[09 OQ-13 :193](../mode-x-er/09-hand-raise-summon.md)（`gesture_detector` の package 配置）とは別ノードだが、同時に裁定する**（別々の答えへの着地を防ぐ） → **条件付き裁定済（[ADR-0012 決定 6](../adr/0012-speed-band-no-l2-best-effort.md)）**: `gesture_detector` と同パッケージの別ノード・package 名は OQ-13 従属（新 package を発明しない＝09:376 維持）・対応表への行追記は実装 PR の DoD へ移管 | 高 |
| **OQ-R3** | **R-26 unit の設計**（[09:426](../mode-x-er/09-hand-raise-summon.md) の要求「INV-2 を破らないことを R-26 相当の unit で pin」）。最低限: ① publisher が `min(帯値, config 運用値, MAX_LINEAR_VELOCITY)` を必ず通る ② `0.0` を publish しない ③ `speed_limit` publish が `cmd_vel` 系トピックへの publish を伴わない（velocity producer 化していない）。加えて ④ `percentage=false` は Nav2 内部で `speed_limit / base_constraints.vx_max` の除算を通るため、①が 0/負にならないことは config fail-closed が守るが oracle にも 1 行置く。独立オラクル + mutation（[.claude/rules/safety.md:7](../../.claude/rules/safety.md) / [architecture/20 §9](../architecture/20-dev-quality-and-testing.md)）。unit は `tests/unit/` に置く（CI 可視性 = [mode-m1/02:56](02-m1-driver-and-watchdog.md) と同じ教訓） → **裁定済（[ADR-0012 決定 7](../adr/0012-speed-band-no-l2-best-effort.md)）: 6 本へ拡張**（クランプ天井は config 運用値でなく**①**へ改訂・⑤①超え非送出〔launch override 縮退構成の oracle〕・②に `V_FLOOR` 未満非送出を追加） | 高 |
| **OQ-R4** | **未検出時の publish 方針の確定**（§3-3 の (a) 能動 publish を推すが未確定）＋ **送出 cadence の確定＝安全要件**（QoS(10) 非 latch に加え、§2-1 ④ の MPPI `reset()` が無活動 **1.0s**（`reset_period` 既定・Humble 固有）で②を破棄するため、**周期送出は必須**で周期は `reset_period` より margin をもって短いこと。または `nav2_params.yaml` で `reset_period` を併せ明示設定）＋ **`0.0` 禁止の実装レベル保証** → **裁定済（[ADR-0012 決定 5](../adr/0012-speed-band-no-l2-best-effort.md)）**: 安定段の能動 publish＋**20Hz** 周期送出＋帯遷移時即時送出＋`reset_period` 明示設定（`setSpeedLimit` は O(1)・状態非破壊を実ソース検証済。なお `fallback()` は reset と同一サイクル内で base 制約の指令を返すため周期送出では防げず＝ADR-0012 §Trade-offs で受容） | **最高（安全要件化・2026-08-30）** |
| **OQ-R5** | **`percentage=false`（絶対 m/s）と `percentage=true`（%）のどちらを採るか。** **どちらのモードも Nav2 側に上限クランプは無く**（§2-2・2026-08-30 確認）、①超過の防止は**両モードとも publisher 側クランプ（OQ-R3）が担う**。真のトレードオフは可読性のみ——絶対値は単位が契約・config・S-SPEED と揃い、% は「①比」への一段変換が挟まる。**[09 OQ-T2 :435](../mode-x-er/09-hand-raise-summon.md)（絶対値 m/s か最速段比の倍率か）と同じ選択の別面だが分母が違う**（SpeedLimit の % は①起動基準値比・OQ-T2 の倍率は最速段比。一致するのは最速段=①のときのみ）＝OQ-T2 の決着を無検証で流用しない → **裁定済（[ADR-0012 決定 8](../adr/0012-speed-band-no-l2-best-effort.md)）: `percentage=false`（絶対 m/s）**。OQ-T2 は独立に未決のまま | 中 |
| **OQ-R6** | ~~`behavior_server` に②が効くか未確認~~ → **効かないことをソースで確認済（2026-08-30・§2-1 ③追補）**: `nav2_core::Behavior` IF に `setSpeedLimit` が無い＝構造的に不達。残る問いは**裁定のみ**——recovery 中は①の基準値まで出る（帯が無効な区間が確実に存在する）ことを ③ L0' 依存で許容するか、対策（recovery 側 param の連動設定等）を足すか → **裁定済（[ADR-0012 決定 9](../adr/0012-speed-band-no-l2-best-effort.md)）: 対策を足さず許容**（なお床は L0'＝凍結契約値であって帯でも①でもない） | 中 |
| **OQ-R7** | **②が `wz`（角速度）も同率でスケールする副作用の許容**（§2-2）。凍結契約に wz 上限は無い（[ADR-0010 §Open 6 :68](../adr/0010-raise-speed-cap-to-platform-max.md)）ため契約違反ではないが、最遅段で旋回が鈍る/最速段で旋回が速くなる挙動をデモ上許容するかは未裁定 → **裁定済（[ADR-0012 決定 10](../adr/0012-speed-band-no-l2-best-effort.md)）: 許容**（「直進のみ減速して旋回を維持」は SpeedLimit 機構では原理的に不可能＝vx_min/vy/wz が同 ratio） | 中 |

**残件（OQ ではない作業）**: ~~[productization/01](../productization/01-commercial-box-map.md) の対応表に `warehouse_m1_driver`（L0'）の行が無い~~ → **解消済（2026-08-28〜30）**: 所有トラック側の [#555](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/555) が対応表へ L0' 行を追加（[productization/01:187](../productization/01-commercial-box-map.md)）、[#556](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/556) が GLOSSARY §3 へ正準エントリを追加。本 doc 起草時（2026-08-28）の残件記述は履歴として取り消し線で保存。

## 7. References（双方向リンク）

**forward（本 doc → 正本）**:

- [mode-x-er/09-hand-raise-summon.md 【2026-08-21 追補④】](../mode-x-er/09-hand-raise-summon.md) — 帯の意味論の正本。とくに [T-6 :399-413](../mode-x-er/09-hand-raise-summon.md)（写像先と制約 1〜4）/ [:408](../mode-x-er/09-hand-raise-summon.md)（正直な限界）/ [T-5 :388-397](../mode-x-er/09-hand-raise-summon.md)（未検出時）/ [T-8 :419-426](../mode-x-er/09-hand-raise-summon.md)（INV-1 / INV-2 の生存確認と R-26 要求）/ [OQ-T3 :436](../mode-x-er/09-hand-raise-summon.md)（本 doc が受ける問い）/ [INV-1 :25](../mode-x-er/09-hand-raise-summon.md)・[INV-2 :57](../mode-x-er/09-hand-raise-summon.md)
- [adr/0010-raise-speed-cap-to-platform-max.md](../adr/0010-raise-speed-cap-to-platform-max.md) — 速度値の正本（[§Decision 1-2 :22-23](../adr/0010-raise-speed-cap-to-platform-max.md) 契約再定義と config 注入 / [§Decision 3 :24](../adr/0010-raise-speed-cap-to-platform-max.md) L0' 維持 / [§Decision 5 :26-29](../adr/0010-raise-speed-cap-to-platform-max.md) 派生再導出 / [§S-SPEED :31-39](../adr/0010-raise-speed-cap-to-platform-max.md) / [§Open :61-68](../adr/0010-raise-speed-cap-to-platform-max.md)）
- [adr/0008-ros2-distro-humble-for-rosmaster-m1.md](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) — distro = Humble（[:16](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）。裏取りを humble ブランチで行う根拠。[:25](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) MPPI Humble リリース（本 doc §2-1 ④で再確認・一致）
- [mode-m1/02-m1-driver-and-watchdog.md](02-m1-driver-and-watchdog.md) — L0'（[:45](02-m1-driver-and-watchdog.md) 必経 / [:53](02-m1-driver-and-watchdog.md) 単一ソース import / [:55](02-m1-driver-and-watchdog.md) `set_speed_limit(0x16)` 採用禁止 / [:56](02-m1-driver-and-watchdog.md) R-26 / [§3 :58-68](02-m1-driver-and-watchdog.md) watchdog）
- [mode-m1/03-joystick-teleop-bringup.md](03-joystick-teleop-bringup.md) — joy 経路（[§3 :28-50](03-joystick-teleop-bringup.md)・C-8 [:48](03-joystick-teleop-bringup.md)・standalone [:50](03-joystick-teleop-bringup.md)）／[mode-m1/01-mode-boundary-and-traffic.md](01-mode-boundary-and-traffic.md)（`traffic_mode: none`）
- [.claude/rules/safety.md](../../.claude/rules/safety.md)（速度上限の強制・R-26 の質）／[.claude/rules/layer-annotation.md](../../.claude/rules/layer-annotation.md)（layer 注記）／[productization/01:180-188](../productization/01-commercial-box-map.md)（正準 layer 対応表）
- 実装側 anchor（**行 pin しない**＝churn するため契約の形で指す）: `nav2_bringup.launch.py` の `_operating_vx_max()` / `RewrittenYaml` `param_rewrites {"vx_max": …}`・`nav2_params.yaml` の `FollowPath.vx_max`・`twist_mux.yaml` の `emergency`/`nav2` 2 入力・`warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`・`warehouse_m1_driver.clamp.clamp_body_velocity`
- **Nav2 一次情報（すべて参照日 2026-08-28・humble ブランチ）**:
  - <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_controller/src/controller_server.cpp>（`speed_limit_topic` 既定 `"speed_limit"` / QoS(10) 購読 / controller への fan-out）
  - <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_msgs/msg/SpeedLimit.msg>（`header` / `percentage` / `speed_limit`・**no-limit = 0.0**）
  - <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_core/include/nav2_core/controller.hpp>（`setSpeedLimit` 純粋仮想）
  - <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_mppi_controller/src/controller.cpp> / <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_mppi_controller/src/optimizer.cpp>（MPPI 実装・constraints スケール）
  - <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_costmap_2d/include/nav2_costmap_2d/costmap_filters/filter_values.hpp>（`NO_SPEED_LIMIT = 0.0`）
  - <https://index.ros.org/p/nav2_mppi_controller/>（Humble 1.1.20）
  - <https://raw.githubusercontent.com/ros-navigation/docs.nav2.org/sphinx_docs/configuration/packages/configuring-controller-server.rst>（Controller Server 設定表の **Humble 期一次原文**（`sphinx_docs` ブランチ）。参照日 2026-08-30。旧 HTML は mkdocs 移行で 404・初稿のミラー確認は本 URL へ置換済）
  - <https://raw.githubusercontent.com/ros-navigation/navigation2/humble/nav2_core/include/nav2_core/behavior.hpp>（`Behavior` IF に `setSpeedLimit` 無し＝recovery へ②不達の根拠。参照日 2026-08-30）
  - `optimizer.cpp` `Optimizer::reset()`（`settings_.constraints = settings_.base_constraints` で②破棄）/ `controller.cpp` `reset_period_` 既定 1.0s（Humble 固有）— URL は上記 nav2_mppi_controller の 2 本と同一。参照日 2026-08-30
- [docs/GLOSSARY.md §11](../GLOSSARY.md) — **runtime speed limit（走行中速度上限）** の 1 エントリを本 doc と双方向で追補済

**backlink**: `docs/mode-m1/README.md`（ファイル表・§関連 ADR・Status）と `docs/README.md`（mode-m1 表）の索引行は**同一ラウンドで整備済**。[09:436 の OQ-T3 行](../mode-x-er/09-hand-raise-summon.md) と [09:408](../mode-x-er/09-hand-raise-summon.md) には本 doc への行内リンクを追加済（行の増減なし）。

---

**【2026-08-30 追補】OQ-R1〜R7 裁定と、検証で確定した Humble MPPI の追加事実**: 3 レーン敵対的検証（repo 整合／Nav2 humble 実ソース HEAD `3c3db59`／L2 実装実態）を経て、§6 の OQ-R1〜R7 を **[ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md)** で一括裁定した（**L2 非経由**・帯＝承認済み envelope 内の best-effort 制御面・クランプ天井は config 運用値から**①**へ改訂・**20Hz** 周期送出）。検証で新たに確定した実装事実 4 点を設計理解の前提として追記する（詳細と受容判断の正本は ADR-0012 §Context/§Trade-offs）:

- (a) **Savitzky-Golay 平滑はクリップ後に走り再クリップしない**（`evalControl` が `applyControlSequenceConstraints` の後に `savitskyGolayFilter` を適用し、制限前の `control_history_` を混ぜる）＝帯遷移直後 ~4 制御サイクルは帯値を約 2 割超過しうる（手計算値・実測回帰は実装スライスの DoD）。**帯値は cmd_vel の厳密上界ではない**。
- (b) **`fallback()`（全軌道衝突時）は `reset()` 直後に同一サイクル内で再 `optimize()` した指令を返す**＝base 制約の指令が周期送出では防げない形で出る（upstream 修正 [PR #5768](https://github.com/ros-navigation/navigation2/pull/5768) は main のみ・Humble 未 backport。goal 実行時 reset の修正 [PR #5165](https://github.com/ros-navigation/navigation2/pull/5165) も同様。reset 後ゼロ速度 [#4545](https://github.com/ros-navigation/navigation2/issues/4545) は wontfix）。
- (c) **任意の dynamic param set が post-callback `reset()` で帯を全解除する**（[#5790](https://github.com/ros-navigation/navigation2/issues/5790)・修正 [PR #5832](https://github.com/ros-navigation/navigation2/pull/5832) は main のみ。走行中の `FollowPath.*` set は ControllerServer 側 callback の dot-name skip で素通り）→ 運用で禁止・20Hz 送出の ~50ms 自己修復で受容。
- (d) **ConstraintCritic は帯を見ない**（initialize 時の param キャッシュ・`settings_.constraints` 非参照）＝帯値を①から下げるほど MPPI の軌道評価と実行速度の乖離が増える（保守側の誤差）。帯値の下限目安は実装時に実測で決める。

(a)〜(d) はいずれも ① ≤ 凍結契約値と ③ L0' に包絡され、**安全床は破れない**——破れうるのは「帯」という運用上の約束のみ（＝帯を安全機構と呼ばない理由）。あわせて**単一 publisher 規律**（`/bot{n}/speed_limit` は帯 publisher 1 本・costmap `filters` に SpeedFilter を入れない・`nav2_route` AdjustSpeedLimit 不使用・相対名維持）を実装スライスの起動時アサート対象とする（ADR-0012 決定 11）。
