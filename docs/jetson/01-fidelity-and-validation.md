# Jetson 忠実度ギャップ分析と実機投入前検証計画（dev/stg → prod de-risk）

作成日: 2026-06-05

> **目的**: 現状ソフトは **Mac M4(arm64) + tiryoh コンテナ(ARM64)** で検証している。これが
> **実 Jetson Orin Nano Super(arm64 Ubuntu 24.04)** にどこまで近似でき／**何が原理的に近似不可か**
> を明文化し、実機到着後にしか潰せない手戻り（性能・メモリ・実時間性が prod で初めて露見）を
> **計画段階で先回りして de-risk** する。実測系は Jetson 到着後の「実機投入前ゲート」で確定するが、
> その**合否基準を本 doc で先に固める**（到着後に即検証できる状態にする）。**本レーンは計画/docs のみ・
> 実機を動かさない**（[`.claude/rules/safety.md`] / 安全ゲートは下記 §4・doc16:213-216）。
>
> **設計正本（着手前 Read 済・file:line）**:
> - 環境分離: [`docs/architecture/19-environments-and-config.md:16-21`](../architecture/19-environments-and-config.md)（dev/stg/prod 行）`:52-54`（base+overlay）`:94`（prod=git タグ clone）。本 doc の要点は doc19 §7（実機投入前ゲート）にも固定。
> - デプロイ前提: [`docs/architecture/17-development-workflow.md:75-91`](../architecture/17-development-workflow.md)（§4.0 別マシン＝clone・`:88` Jetson 実機は別途 `git clone`）。
> - リスク台帳: [`docs/shared/07-research-notes.md:242`](../shared/07-research-notes.md)(R-37)`:243`(R-38)`:249`(R-39)`:250`(R-40)`:251`(R-41)`:252`(R-42)`:253`(R-43)`:258`(R-48)`:153`(R-02)`:155`(R-08)`:23`(Isaac ROS)。
> - 安全4層・実時間目標: [`docs/architecture/12-infrastructure-common.md:47-48`](../architecture/12-infrastructure-common.md)（Hard/Soft RT）`:75-84`（Layer0/1）`:483`（50ms/100ms は非ハードRT）`:254`（battery scale #44）。
> - メモリ二段構え: [`docs/architecture/06-implementation-phases.md:89-102`](../architecture/06-implementation-phases.md)（段階1 Mac Docker 6GB 近似 / 段階2 Jetson 実測）。
> - ハードウェア: [`docs/shared/02-hardware-design.md:52-164`](../shared/02-hardware-design.md)（Jetson Orin Nano Super 準備）`:180`（RPLiDAR USB）。
> - 開発環境定義: [`docs/architecture/03-software-architecture.md:255-274`](../architecture/03-software-architecture.md)（Mac 開発機 :257 / Jetson 実行機 :266）。
> - 実装: [`deploy/jetson/`](../../deploy/jetson/)（systemd unit・スクリプト・env 雛形）/ 正本手順 [`docs/setup/jetson-deploy.md`](../setup/jetson-deploy.md)。

---

## 1. 要旨 — なぜこの doc が要るか

- **追い風（忠実度が高い側）**: CPU アーキは **Mac M4 も Jetson Orin Nano Super も ARM64**（doc06:91
  「Mac M4 と Jetson はどちらも ARM64」）で、tiryoh コンテナも **ARM64-native**（doc03:262）、Jetson は
  **ROS 2 Jazzy / Ubuntu 24.04**（doc03:270）・micro-ROS も Jazzy 対応確認済（doc07:22）で揃う。
  x86 dev マシンより**命令セット・依存ビルドの忠実度が高い**。ROS ノードロジック・凍結契約・launch 合成・
  pytest はこの一致のおかげで Mac で高忠実に検証できる。
- **逆風（原理的に近似不可な側）**: 一方、以下は **Mac/Docker では原理的に検証できない**。実 Jetson でしか
  露見しない：
  - **GPU/CUDA**（Isaac ROS・GPU 加速 Nav2/SLAM。Mac に CUDA は無い。doc07:23 / doc02:90）。
  - **実時間性**（Docker Desktop は Mac 上で軽量 VM 経由＝50ms Guardian / 100ms State Cache の jitter が
    実機と別物。R-40＝doc07:250 の通り rclpy の GC/GIL スパイクで最悪応答時間は有界でない。`gc.disable()` も
    best-effort。doc12:483 が「50ms/100ms は設計目標値でハードRT保証ではない」と明記）。
  - **micro-ROS WiFi UDP / 実センサ**（MS200・encoder・battery は実機のみ。**R-37＝2台同時接続**＝doc07:242）。
  - **メモリ予算**（Jetson Orin Nano は **8GB を CPU/GPU でユニファイド共有**＝doc06:100。Mac 16GB / Docker
    `--memory` 制限では再現できない。主方針 Mode C の **Open-RMF が 8GB に載るか＝R-38＝doc07:243**）。
- **結論**: これらは「dev(Mac) でも stg(クラウド sim) でも近似不可」＝**実 Jetson 上の "実機投入前ゲート"**
  （ロボット非駆動の bench bring-up）でしか潰せない。本 doc は §2 でギャップを切り分け、§4 で**そのゲートの
  合否基準**を先に固める。環境間の位置づけは §3、deploy/jetson の整合は §5、残課題は §6。

---

## 2. 忠実度ギャップ表（領域 × Mac/Docker 検証可否 × 実機固有 × 確認場所 × 合否/根拠）

凡例: ◯ = dev(Mac/Docker) で高忠実に検証可 ／ △ = 近似のみ可（確定値は実機） ／ ✕ = 原理的に近似不可（実機必須）。

| # | 領域 | dev(Mac) | 近似不可の理由（実機固有） | 確定する場所 | 合否基準 / 根拠 doc |
|---|---|:---:|---|---|---|
| F1 | ROS ノードロジック・凍結契約・launch 合成 | ◯ | arm64 一致＋偽トピック/偽 `state.json` で完全独立検証可（doc16:213-216） | dev（pytest/CI） | unit/CI 緑。`scripts/check_consistency.py` 0 ERROR |
| F2 | config overlay（`WAREHOUSE_ENV`・base+prod） | ◯ | パス解決は純 Python（`paths.py`）。prod=`/run/warehouse` は env 解決で再現可 | dev（unit） | `WAREHOUSE_ENV=prod` で `/run/warehouse` 解決（doc19:18 / jetson-deploy.md:157-158） |
| F3 | 2台 Gazebo 自律走行 E2E | ◯ | headless `gz sim`＋`ros_gz_bridge` 環境成立（spike GO, doc06:112）。tiryoh は ARM64-native（doc03:262）。実 bot1/bot2 E2E は sim track #8/#156 で進行（doc06:112） | dev（tiryoh Docker） | 2台が衝突せず巡回（sim。実機性能は別） |
| F4 | **GPU / CUDA**（Isaac ROS・GPU 加速 Nav2/SLAM・GPU costmap） | ✕ | **Mac に CUDA 無し**。Isaac ROS は Jetson 専用、release-3.x は未検証（doc07:23 / doc02:90） | **実機ゲート G4**（Jetson） | CPU 版 Nav2×2 で巡回が実時間成立。GPU 利用は載れば加点（doc06:100 ユニファイド食合せ計測） |
| F5 | **実時間性**（50ms Guardian / 100ms State Cache の jitter） | ✕ | Docker Desktop は Mac 上 VM 経由でスケジューラが別物。R-40（doc07:250）GC/GIL スパイク＝最悪応答有界でない。doc12:483 が非ハードRT明記 | **実機ゲート G3**（Jetson） | §4 G3 の jitter 合否（p99 / max・stale 検出）。最終防衛は Layer0(doc12:75-78) |
| F6 | **micro-ROS WiFi UDP 2台同時**（R-37） | ✕ | 実 ESP32×2 + WiFi が必要。**XRCE `client_key` 衝突**で片方向喪失（doc07:242）。host spike は distinct key で実証も loopback 止まり | **実機ゲート G2**（Jetson+実機） | §4 G2＝distinct `client_key` で単一 Agent(:8888) 2台双方向 OK（doc07:79,242 / firmware/spike/RESULT.md） |
| F7 | **LaserScan の micro-ROS UDP 転送**（MTU/フラグメント, R-43） | ✕ | 900点×float≒3.6KB/scan を UDP MTU 512B で 2台分常時。host spike は小 `Int32` のみ＝未検証（doc07:253） | **実機ゲート G6**（Jetson+実機） | scan 欠落/遅延なく `/bot{n}/scan` 到達。不足ならダウンサンプル/USB 有線（doc07:253） |
| F8 | **実センサ精度**（MS200 測距 R-41 / encoder / battery scale #44） | ✕ | 物理センサ固有。MS200 dToF 誤差 ±1-3cm が地図 1cm 解像度と同オーダー（doc07:251）。battery `percentage` スケールはドライバ依存（doc12:254） | **実機ゲート G5**（Jetson+実機） | §4 G5＝測距誤差実測→地図解像度確定 / `safety.battery_percentage_scale` 確定（doc12:254 #44） |
| F9 | **メモリ予算**（8GB ユニファイド・Open-RMF R-38） | △ | Mac M4/Docker `--memory=6g` で**段階1 近似は可**（doc06:90-93）。だが **CPU/GPU 8GB 食合せ**（doc06:100）**・JetPack 常駐 2-2.5GB**（doc06:93）は再現不可（R-38＝doc07:243） | 段階1 dev / **確定値=実機ゲート G1** | §4 G1＝`free -h` 残RAM ≥500MB（未満は Open-RMF 断念＝Mode B 格下げ、doc06:98＋doc07:243(R-38) Go-No-Go） |
| F10 | **WiFi 唯一経路・同時通信**（R-08 / DDS multicast R-48） | ✕ | テザリング/ルータ 1台で micro-ROS×2 + LLM API + Langfuse を同時（doc02:77-84）。WiFi multicast drop で DDS discovery 不成立（doc07:155,258） | **実機ゲート G6**（Jetson+実機） | §4 G6＝2台接続中に LLM API 往復が安定。discovery 断なら Discovery Server/固定 `ROS_DOMAIN_ID`（doc07:258） |
| F11 | **Layer 0 速度クランプ・近接 estop**（MCU 実機） | ✕ | ESP32/MCU 内の最終防衛線。ROS 側 `cmd_vel` に依らず ≤0.3 m/s クランプ（doc12:75-78）は実ファーム/実機でのみ検証 | **実機ゲート G0**（実機・最優先） | §4 G0＝≤0.3 m/s クランプ・近接 e-stop が実機で有効（doc12:75-78 / safety.md） |
| F12 | **熱スロットリング・持続負荷**（R-09） | ✕ | ファン付きヒートシンク同梱でも 25W 持続負荷でクロック throttle しうる（doc02:62-63）。Mac M4 と熱設計が別物＝再現不可。単一計算ノード障害で全機能喪失（doc07:177 R-09） | **実機ゲート G4 併設**（Jetson・持続負荷） | `tegrastats` で持続 Nav2×2 負荷10分後も throttle せず G3 jitter が許容内（doc07:177 / doc02:62-63） |

> **凍結値の出所**: 速度上限 0.3 m/s は `warehouse_interfaces` の `MAX_LINEAR_VELOCITY`（凍結契約・doc12:77 と一致）、
> battery しきい値・スケールは `warehouse_interfaces.safety`（#44・doc12:254）、inflation 半径は
> `warehouse_description/robot_dimensions.py`（R-42 で 0.075、Phase 1 実測）＝**いずれも docs ではなく凍結ソースが正本**。
> 本 doc はそれらを restate せずリスク台帳（doc07）の行で参照する。

---

## 3. 切り分け — 検証可能 / 原理的に近似不可（issue #127 DoD）

### 3.1 Mac/Docker(dev) で検証可能（実機不要・到着前に完結）

ROS ノードロジック・**凍結契約**（`warehouse_interfaces` schema/IF）・**launch 合成**（`bringup.launch.py` の
構文/引数解決）・**systemd unit 定義**（`deploy/jetson/` の静的検査）・**pytest**（安全機構 unit＝doc16:215）・
**config overlay**（`WAREHOUSE_ENV` の base+prod 解決）・**2台 Gazebo 自律走行 E2E**（環境成立・spike GO
doc06:112、sim 範囲。実 bot E2E は sim track #8/#156）。→ F1-F3。**ARM64 三者一致が効く領域**（doc06:91）。

### 3.2 原理的に近似不可（実 Jetson の "実機投入前ゲート" 必須）

**GPU/CUDA**(F4)・**実時間性 jitter**(F5)・**micro-ROS 2台 WiFi UDP**(F6)・**LaserScan UDP MTU**(F7)・
**実センサ精度**(F8)・**メモリ確定値（ユニファイド食合せ）**(F9 の確定値)・**WiFi 同時通信/discovery**(F10)・
**Layer 0 estop**(F11)・**熱スロットリング/持続負荷**(F12)。→ §4 のゲートで合否を取る。**Mac では「無い／別物／未接続」のため値が出ない**のが本質。

---

## 4. 実機投入前ゲート（Jetson 到着後・prod 昇格前・合否基準付き）

> **位置づけ**: dev(Mac, F1-F3) と stg(クラウド sim・doc19:17) は**ソフト/統合の忠実度**を閉じるが、
> **ハードウェア忠実度（§3.2）は閉じられない**。それを閉じるのが本ゲート＝**実 Jetson 上の bench bring-up
> （ロボット非駆動から段階的に）**で、**prod（実ロボット駆動・撮影）への昇格の前提条件**。
> 各ゲートは「いつ／どこで／合否基準／不合格時の分岐」を持つ。**G0 が最優先・必須の安全ゲート**
> （未通過なら `systemctl enable --now` しない＝`install.sh` は導入のみ。jetson-deploy.md:23-26）。

| ゲート | 何を測る | いつ / どこで | 合否基準（PASS） | 不合格時の分岐 | 根拠 doc |
|---|---|---|---|---|---|
| **G0 安全**（必須・最優先） | Layer 0 速度クランプ ≤0.3 m/s・近接 e-stop / Layer 1 Emergency Guardian unit | Jetson+実機 1台接続後すぐ。motion 有効化の**前** | ROS 側で 0.3 超指令を出しても MCU が ≤0.3 にクランプ・近接停止。Guardian unit テスト緑 | **昇格不可**。原因解消まで motion 系 unit を enable しない | Layer0=doc12:75-78 / Guardian unit=doc16:215 / 昇格ゲート=doc19:21・safety.md / jetson-deploy.md:15-26 |
| **G1 メモリ** | 全スタック起動時の `free -h` 残RAM（ユニファイド食合せ込み） | Jetson 実機・段階2（doc06:95-102） | Nav2×2 + State Cache + Guardian + Bridge 起動で**残RAM ≥500MB**。Mode C は + Open-RMF で再測 | 残<500MB → **Open-RMF 断念＝Mode B 格下げ** or 別マシン offload（Go/No-Go） | doc06:89-102,:98,:100 / doc07:243(R-38),:153(R-02) |
| **G2 micro-ROS 2台** | 単一 Agent(:8888) で ESP32×2 を WiFi UDP 双方向 | Jetson+実機 2台（Phase 2 後半・host spike 前倒し済） | 両 ESP32 に **distinct `client_key`** 設定で 2 session 独立・pub/sub 双方向 OK | key 差で不可 → **USB 有線**（#21 Case5）。2 Agent/別ポートは降格 | doc07:79(T5),:242(R-37) / firmware/spike/RESULT.md |
| **G3 実時間性 jitter** | Guardian 50ms / State Cache 100ms 周期の実測ヒストグラム | Jetson 実機（R-40＝Phase 0.5(Jetson)） | 周期の **p99 / max** がデッドライン内に収まる。`gc.disable()` 適用後に外れ値が許容内。pose stale 検出が機能 | hot path を Python から外し **C++ `nav2_collision_monitor` + ESP32(Layer0)** へ委譲（#126） | doc07:250(R-40),:249(R-39) / doc12:47,:483,:502-553 |
| **G4 nav2/SLAM 性能（＋熱 R-09）** | CPU 版 Nav2×2 + AMCL + SLAM Toolbox の実時間追従（GPU 加速は任意）＋ `tegrastats` 熱クロック | Jetson 実機（G1 後・**持続負荷10分**） | 2台が経路追従・障害物回避を実時間で破綻なく（CPU で成立・GPU は載れば加点）。**10分持続負荷後もクロック throttle 無し**（熱定常で G3 jitter 維持） | 実時間割れ → 周期/解像度調整・GPU costmap 検討。throttle → ファン/ヘッドレス/省電力 mode | doc02:90,:138,:62-63 / doc06:100 / doc07:23,:177(R-09) |
| **G5 実センサ精度** | MS200 測距誤差 / encoder / battery `percentage` 実スケール | Jetson+実機（Phase 1-2） | 測距誤差を実測→**地図解像度を誤差の2-3倍**へ。`safety.battery_percentage_scale` を実測で確定 | 余裕不足 → 通路幅/inflation 再設計（R-41/R-42）。scale 未確定は fail-fast（doc12:254） | doc07:251(R-41),:252(R-42) / doc12:254 |
| **G6 WiFi 同時通信** | micro-ROS×2 + LLM API + Langfuse + LaserScan UDP の同時安定性 | Jetson+実機（Phase 1, T3 併せ） | 2台接続中に LLM 往復・scan 到達が断なく安定。DDS discovery 成立 | scan 欠落 → ダウンサンプル/USB。discovery 断 → Discovery Server/固定 `ROS_DOMAIN_ID` | doc07:155(R-08),:253(R-43),:258(R-48) / doc02:77-84 |
| **G7 Hermes(GCP) 到達性 + E2E** | prod GCP Hermes へ Jetson Bridge が到達・司令官サイクル | Jetson 実機（G0-G2 後・撮影前リハ＝stg 相当） | `healthcheck.sh` で Hermes 到達 ◯。Bridge→Hermes 認証（`API_SERVER_KEY` 同値）で司令官サイクル成立 | 到達不可 → ネットワーク/Gateway 確認（secrets は触らない・read-only） | doc19:18,:86 / healthcheck.sh:61-70 / deploy/jetson |

> **ゲートと昇格の関係**: G0 は無条件必須。G1（メモリ Go/No-Go）が **Mode C 採否**を分岐する最大の🔴
> （R-02/R-38）。G2-G6 は実機固有値の確定。G7 は撮影前リハ（stg 相当の最終確認）。**すべて PASS で
> prod（実ロボット駆動・撮影）へ昇格**。Jetson 到着前は本表の合否基準を凍結し、到着後に値を埋める。
> `deploy/jetson/bin/preflight.sh --arrival --gates G0,G1,G7` は本表のうち自動判定できる部分を補助する。
> Layer 0 速度クランプ/e-stop、実機 2台通信、測距誤差などの実測は MANUAL として記録し、script の PASS だけで
> prod 昇格とはしない。

---

## 5. deploy/jetson 整合確認（systemd ↔ doc19 / doc17 §4）

既存 `deploy/jetson/` の scaffold（unit 6 + スクリプト 3 + env 雛形）を doc19/doc17 §4 と突合した結果。
**いずれも整合（修正不要）**。実機なしの検証手順は §5.2。

### 5.1 整合チェック結果

| 項目 | 期待（正本） | deploy/jetson 実装 | 判定 |
|---|---|---|---|
| prod=別マシン clone | doc17:88（Jetson は別途 `git clone`）/ doc19:94（git タグ固定） | README:38-40・install.sh:12・jetson-deploy.md:43-51（`/opt/warehouse` clone・ExecStart 書換） | ◯ |
| prod runtime dir | doc19:18（`/run/warehouse` systemd `RuntimeDirectory`） | 各 data unit が `RuntimeDirectory=warehouse`+`Preserve=yes`（state-cache/safety/bridge） | ◯ |
| 起動順 | doc02:138 ノード一覧・doc12 層構造 | microros → state-cache → safety → nav2 → bridge（`After=` 連鎖。nav2→safety は `BindsTo=`） | ◯ |
| **安全トポロジ** | doc12:80-84（Guardian が motion を止める）/ safety.md | nav2 が safety を **`BindsTo=`(+`After=`)**＝guardian クラッシュで nav2 停止（`Requires=` 不採用の理由を unit コメントに明記） | ◯ |
| 安全ゲート | doc19:21（estop テスト通過後のみ）＋ doc16:215（安全機構 unit 必須） | `install.sh` は導入のみ＝**enable/start しない**。motion はゲート後手動 | ◯ |
| Hermes は GCP（Jetson でない） | doc19:18,:86（`34.4.104.112`） | bridge unit/README/healthcheck が GCP を read-only 言及（Jetson に Hermes を置かない） | ◯ |
| micro-ROS transport | doc02:81（WiFi UDP）/ G2（distinct `client_key`） | microros unit が `udp4 --port ${MICROROS_PORT}`（既定 8888） | ◯（key 差は **ファーム側**で設定＝G2 で確認） |
| traffic_mode 単一ソース | doc19:54（config 単一ソース）/ 11a:317（Mode C） | env.example が prod=`open-rmf` を `config/prod/warehouse.yaml:13` と同期せよと明記。`bringup.launch.py` は config を既定値に直読み（#75/PR#93・#156/PR#162 着地済） | ◯ |
| **prod launch 引数（二重起動防止）** | prod=実機（gz 無し）＋ LLM は専用 unit `warehouse-bridge.service` | `warehouse-nav2.service:29` が `sim:=false llm:=false` を固定＝**nav2-only**（`bringup.launch.py` 既定 `sim:=true`/`llm:=true` は Mac capstone 用、:148-149,154-155） | ◯（#156 cross-lane→#127 で反映） |

### 5.2 実機なしでできる検証手順（README に追記済）

- `systemd-analyze verify deploy/jetson/systemd/*.service deploy/jetson/systemd/*.target`（unit 構文・依存の静的検査。ROS 不要）。
- `bash -n` / `shellcheck deploy/jetson/bin/*.sh`（スクリプト構文）。
- env 解決（prod=`/run/warehouse`）は既存 unit テストで回帰（`WAREHOUSE_ENV=prod`）。
- `deploy/jetson/bin/preflight.sh --offline`（上記を束ねる到着前 check。Linux 専用コマンドが無い host では SKIP として報告）。
- **実機投入は §4 の各ゲート通過後**（G0 安全ゲート最優先）。

---

## 6. 残課題・未決・暫定値（隠さない）

- **実機実測待ち＝ハードウェアゲート**: §4 G0-G7 の値は **Jetson 到着後に確定**（CUDA/jitter/2台接続/メモリ確定値/
  測距誤差/WiFi 同時通信）。本 doc は到着前に**合否基準のみ凍結**した（値は未取得）。
- **熱スロットリング（R-09・doc07:177）**: ファン付きヒートシンク同梱（doc02:62-63）でも 25W 持続負荷での
  クロック throttle は Mac で再現不可。**G3 jitter / G4 性能は熱定常状態で測る前提**＝cold-start で通っても
  持続負荷で落ちうる。到着後に `tegrastats` で確認（F12 / G4 併設）。
- **stg の位置づけ**: doc19:17 の stg は現状 **クラウド sim（RunPod Isaac / cloud sim）＝ハードウェア非含**。
  本ゲート（実 Jetson bench）は **stg と prod の間の rung**＝「実機投入前ゲート」として doc19 §7 に固定した
  （stg を実機含みに再定義はしない＝additive・既存契約不変）。
- **暫定しきい値（実機で確定予定）**: `safety.pose_freshness_timeout`（既定 1.0s 暫定・doc12:510）／
  `safety.battery_percentage_scale`（Phase 1 実測・doc12:254）／ `ROBOT_RADIUS`（Phase 1 実測・R-42）／
  残RAM 500MB 閾（doc06:98 の Go/No-Go 値）。いずれも到着後に G1/G3/G5 で更新。
- **prod nav2 起動（本 PR で反映・#156 cross-lane→#127）**: `bringup.launch.py` は #75/PR#93・#156/PR#162 で
  フルスタック合成され、既定 `sim:=true`/`llm:=true`（Mac capstone・:148-149,154-155）。prod は
  `warehouse-nav2.service:29` が `sim:=false llm:=false` を固定して **nav2-only** 化し、gz sim / llm・nav2 bridge
  の二重起動を防ぐ。G4（nav2/SLAM 性能）の実効は実機到着後＝§4。
- **Phase 1 追加 unit**: `warehouse_mcp_server` / `warehouse_nav2_bridge`(Mode A/B) / WO Bridge は別トラック
  実装後に unit 化（jetson-deploy.md:140-145）。LLM Bridge 稼働には MCP Server が必要。

---

## References

- [doc19 環境分離](../architecture/19-environments-and-config.md) §1 環境一覧 / §7 実機投入前ゲート（本 doc の要点固定）
- [doc17 開発の進め方](../architecture/17-development-workflow.md) §4.0（別マシン＝clone・Jetson）
- [doc07 リサーチノート](../shared/07-research-notes.md)（R-37/R-38/R-39/R-40/R-41/R-42/R-43/R-48/R-02/R-08）
- [doc12 共通インフラ](../architecture/12-infrastructure-common.md)（4層安全・50ms/100ms 目標・#126 委譲）
- [doc06 実装フェーズ](../architecture/06-implementation-phases.md)（メモリ検証二段構え）
- [doc02 ハードウェア](../shared/02-hardware-design.md)（Jetson Orin Nano Super / センサ）
- [docs/setup/jetson-deploy.md](../setup/jetson-deploy.md)（prod デプロイ正本手順）/ [deploy/jetson/](../../deploy/jetson/)（実装）
- `.claude/rules/safety.md`（速度上限・estop・secrets 非コミット）
