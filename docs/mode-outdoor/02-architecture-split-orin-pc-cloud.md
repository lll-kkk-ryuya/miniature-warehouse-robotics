# 02 — Orin / PC / クラウドの分担（architecture split）と遠隔操作リンク

作成日: 2026-09-12
Status: **記載済（設計値・裁定待ち項目は明記）**。§2 の分担表は 2026-09-12 所見の草案を、同日のエージェントチーム（アーキ・安全・通信レーン）の報告で補強したもの。[00 §4](00-mission-and-scope.md) の裁定後に「草案」注記を外す。repo の pin は執筆時に実 Read、外部一次情報は URL + 参照日（[D]/[L]/[I] の凡例は [03](03-localization-gnss-and-ekf.md) と同じ）。

> 正本ルート: [mode-outdoor/README](README.md)。レイヤ定義は [productization/01 §レイヤ annotation 対応表](../productization/01-commercial-box-map.md:174)、時間階層（Hard / Soft / Non-RT）は [architecture/12:47](../architecture/12-infrastructure-common.md:47)、環境分離は [architecture/19:16](../architecture/19-environments-and-config.md:16)。図解は [outdoor-architecture-tree.html](outdoor-architecture-tree.html)（09_Remote_Operation）と [outdoor-localization-perception-flow.html](outdoor-localization-perception-flow.html) §4。

## 0. 位置づけ

屋外走行では「車載（Orin）で閉じるもの」「法的に人が握るもの（PC）」「無くても走れるもの（クラウド）」を最初に分ける。分け方を誤ると、通信断のたびに法の枠（[01 §5](01-legal-envelope-japan.md) 自動操縦の除外）から外れるか、安全でない停止が起きる。

## 1. 分担の 3 原則

1. **通信が切れても安全に止まれる機能は Orin に置く**（自己位置・局所計画・障害物・信号検出・停止 producer・物理非常停止）。
2. **法的に人が握る停止・介入は PC（遠隔操作者卓）に置き、常時接続を前提にする**（監視・非常停止・手動 takeover・横断承認）。PC からの heartbeat が途絶えたら Orin 側が止める（[05 §3](05-safety-envelope-and-intervention.md)）。
3. **無くても走れる助言・記録はクラウドに置く**（LLM / Gemini ER の状況説明・NTRIP 補正・Langfuse）。切れたら「助言なし」に縮退して走行を継続する。

## 2. 分担表

| 置き場所 | 機能 | layer / 時間階層 | リンク断時の挙動 |
|---|---|---|---|
| **Orin（車載）** | `navsat_transform` + 2 段 EKF（local: wheel + IMU（+ MOLA-LO）／ global: + GNSS）。`map` は datum 固定の局所直交系（[03 §2](03-localization-gnss-and-ekf.md)） | 自律走行（安全層外＝[23 §1](../architecture/23-perception-and-localization.md:21)） | 自己完結 |
| | Nav2 MPPI + rolling global costmap + waypoint 再生（Humble: L3 compile で `fromLL` 変換 → 既存座標 goal・[03 §3](03-localization-gnss-and-ekf.md)） | L1 Navigation | **停止**（リンク断 watchdog が `stop_request` を engage＝[05 §3](05-safety-envelope-and-intervention.md)。自律継続は法の枠外＝[01 §5](01-legal-envelope-japan.md)） |
| | 屋外知覚: 前方 OAK-D（信号・歩行者）・下向き OAK-D → cliff detector → `/bot1/cliff_scan`・`pointcloud_to_laserscan`（[04](04-perception-sidewalk-and-signals.md)） | 自律走行（costmap 入力）/ L4 知覚（信号・歩行者） | 自己完結 |
| | 横断ゲート（L2 restrict-only profile: 青 かつ 点滅なし かつ 承認トークン。fail-closed）（[05 §4](05-safety-envelope-and-intervention.md)） | L2 Governance | 承認が来ない = 渡らない |
| | 新規 producer: リンク断 watchdog・GNSS 品質ゲート・ジオフェンス・物理非常停止の GPIO latch（[05 §3](05-safety-envelope-and-intervention.md)） | L1 Safety | 停止 |
| | **`operator_link_node`（新・別プロセス・別 port）**: WS 制御 / 映像 2 接続（v2.1）の Orin 側終端。teleop 入力の再生・`stop_request`・横断承認・heartbeat（§2-1） | L4 入力側 + L1 stop producer | リンク断＝watchdog へ |
| | `video_encoder`（software x264 / MJPEG。Orin Nano に NVENC は無い＝NVIDIA 公式 [L]） | 観測面 | 解像度を落として CPU 予算を守る |
| | `web_bridge`（既存・observe-only・**無改変**） | 観測面 | 観測欠落のみ |
| **PC（遠隔操作者卓）** | 映像・地図上位置・状態の監視、**非常停止・手動 takeover（前進・後退・停止・加減速・右左折の全挙動）・横断承認**。web/console とは**別アプリ**（§2-1） | 観測面 + L1 stop producer | **法的必須層**。heartbeat が届かなければ Orin 側が止まる |
| | 固定経路の記録（teach）と compile（`fromLL` → x,y 焼き込み）・route store（[03 §3-3](03-localization-gnss-and-ekf.md)） | L3 入力 | 投入済みミッションは Orin が保持 |
| | rosbag / run record（[jetson/03](../jetson/03-build-deploy-run-and-run-records.md)）。Tailscale over LTE（[jetson/02 §9.7](../jetson/02-remote-access-and-dev-link.md:357)） | 観測面 | 記録欠落のみ |
| **クラウド** | Hermes Gateway（GCP）+ LLM / Gemini ER: 状況説明・例外時の選択肢提示・遠隔者向け要約。**信号判定の権威にしない**（[04 §4](04-perception-sidewalk-and-signals.md)） | L4 Non-RT | 「助言なし」に縮退して走行継続 |
| | NTRIP 補正配信（外部サービス。QZSS CLAS なら不要＝[03 §4-1](03-localization-gnss-and-ekf.md)）、Langfuse・ログ集約 | 観測面 | RTK が float に落ちれば品質ゲートが減速（best-effort）・ロストで停止 |

### 2-1. 遠隔操作リンクの置き場（doc22 の不変条件との整合）

[architecture/22](../architecture/22-web-observability.md) は **ブラウザ→ロボット操作を持たない**ことを非ゴールとして固定し（[22:24](../architecture/22-web-observability.md:24)）、`web_bridge` が actuation sink への publisher / client / forwarder を**ゼロ**生成することを R-26 unit で証明する（[22:283](../architecture/22-web-observability.md:283)）。実装も「every route is GET or a receive-only WebSocket … no actuation client anywhere」（[app.py:7-12](../../ws/src/warehouse_web_bridge/warehouse_web_bridge/app.py:7)）で、`tests/unit/test_web_bridge_noactuation.py` が AST で固定する [D]。さらに doc22 は「control gateway は物理的に別プロセス / 別 port 必須」（[22:99](../architecture/22-web-observability.md:99)）・「WO 画面と web/console は別アプリ（observe-only と control の安全 posture 分離）」（[22:369](../architecture/22-web-observability.md:369)）と先に釘を刺している。**屋外の遠隔操作リンクは doc22 の例外ではなく、doc22 が予約していた席に座る**。

| 面 | 実体（名称は未凍結・`OQ-OD23`） | 権限 | R-26 との関係 |
|---|---|---|---|
| Orin | **`operator_link_node`**（新 package・別プロセス・別 port） | **actuation あり**: teleop 入力の再生（`/joy` 相当 → 既存 teleop 経路 → `/bot1/cmd_vel`）・`/operator/stop_request` の engage / clear・横断承認トークン | `web_bridge` に触れない＝R-26 unit は無改変で緑のまま。**運ぶ意味を閉集合で列挙**（heartbeat / teleop / stop_request / 横断承認 / テレメトリ / 映像）し、新 package 側にも R-26 相当の AST pin を課す（`OQ-OD24`） |
| Orin | `web_bridge`（既存） | observe-only（不変） | テレメトリ・events はここ |
| Orin | `video_encoder` | stream out のみ | actuation sink ではない |
| PC | 操作コンソール（`web/console` とは**別アプリ・別 owner**） | 操作 UI・非常停止・横断承認 | [22:369](../architecture/22-web-observability.md:369) の先例に一致 |

なぜ `web_bridge` に送信可能な WS を足さないか: (a) 「no POST/PUT/DELETE route」は AST pin されており、送信可能な WS を 1 本足した時点で R-26 unit の意味が壊れる [D]。(b) 観測面はクラウド断で落ちてよいが操作面は法的必須層＝**縮退方針が逆向き**の 2 機能を 1 プロセスに置くと片方の障害が他方を巻き込む [I]。

### 2-2. 法要件の写像（正本は [01 §5 / 追補②](01-legal-envelope-japan.md)）

| 法要件 | 置き場 | 備考 |
|---|---|---|
| 遠隔の人が常に**操作**できる（前進・後退・停止・加減速・右左折） | PC 卓 UI → WS → `operator_link_node` → teleop 経路 | **停止だけでは不足**。既存 `teleop_joy` の deadman / `/joy` 鮮度 0.6 s / latch 再アーム（[mode-m1/03:49,51,52](../mode-m1/03-joystick-teleop-bringup.md:49)）の**純ロジックを再利用**し、入力源だけ WS に差し替える（`OQ-OD27`） |
| **見通し外からの操作**（型式認定試験は「無線は直接目視で確認できない場所から」＝[01 追補②](01-legal-envelope-japan.md)） | `video_encoder` → 同一 WS | **映像は法的前提**。映像断＝操作不能＝リンク断扱いにするか（`OQ-OD26`） |
| 通信断で止まり、止まったまま | リンク断 watchdog → `stop_request` engage（[05 §3](05-safety-envelope-and-intervention.md)） | 解除は再アーム条件経由のみ＝自動復帰しない |
| 届出に「通信遅延・通信断絶時の制御方法」を記載 | [05 §3](05-safety-envelope-and-intervention.md) の閾値と挙動が**届出文面の原本** | 値を 2 箇所に書かない（§3-3） |

## 3. 通信（LTE）

### 3-1. DDS を LTE / WAN に流さない（一次情報）

eProsima Fast DDS 公式は Simple Discovery の欠点として「ノード追加で交換パケットが急増しスケールしない」「Multicast を要求し WiFi 等で信頼できない」を明記し、Discovery Server を代替に挙げる [L]。→ 本 doc の方針「DDS を LTE に流さない」は裏が取れた。tailnet（VPN）でも discovery コストは消えない [I]。

### 3-2. 選択肢（Humble）

| 選択肢 | 可否 | 評価 |
|---|---|---|
| `zenoh-bridge-ros2dds` | 可（distro 非依存の配布）[L] | 強力だが **ROS グラフ全体を透過的に運ぶ**ため、意図せず actuation topic が WAN 越しに開通しうる。R-26 / 原則 P2 との相性が悪い [I] |
| `rosbridge_suite` WS | 可（humble 2.0.8）[L] | 任意 topic への publish が原理的に可能。doc22 が [22:25](../architecture/22-web-observability.md:25) で「rosbridge / Foxglove をブラウザに置かない」と既に不採用 [D] |
| **カスタム WS（既存 web_bridge パターン）** | 可 | FastAPI / uvicorn + rclpy の共存パターンが land 済。**運べる意味を設計者が列挙できる＝安全境界が閉じる** |
| Tailscale | **導入済**（[jetson/02 §9.7](../jetson/02-remote-access-and-dev-link.md:357)・CGNAT / テザリング可・DERP フォールバック）[D] | トランスポート層の答えは出ている。NAT 越え・認証・暗号化を自前で作らない |

### 3-3. Phase 1 の最小構成

1. **トランスポート = Tailscale（既存）**。
2. **アプリ層 = カスタム WS を「制御」と「映像」の 2 接続に分ける**（2026-09-14 v2.1・[09 §2-d](09-external-review-v3-response.md)。同一 `operator_link_node`・web_bridge とは別プロセス別 port）。制御接続が運ぶもの: ① heartbeat ② teleop 入力 ③ `stop_request` ④ 横断承認 ⑤ 重要状態（小さな有界キュー・有限の指令期限・再接続時に駆動指令を破棄）。映像接続: ⑥ 映像 + 映像時刻（古いフレームは捨てる・解像度 / 帯域を適応）。テレメトリはベストエフォートで制御をブロックしない。これ以外は運ばない。
3. **映像 = MJPEG over WS で開始 → x264（ultrafast / zerolatency）→ WebRTC は Phase 2**。Orin Nano に NVENC は無く libx264 が公式推奨（1080p ultrafast で ≈ 99 fps・CPU 1 コア 49 % の公式実測 [L]）→ 640×360@15 fps は十分に余裕 [I]。LTE 上りは変動が大きいので Phase 1 は**帯域を使い切らない絶対値で固定**し適応制御は入れない。

| 方式 | 640×360@15 fps 概算 [I] | CPU | Phase |
|---|---|---|---|
| MJPEG over WS | ≈ 2〜3 Mbps | 極小 | 1 |
| x264 over WS | ≈ 0.4〜0.8 Mbps | 小 | 1.5 |
| WebRTC（`webrtcbin` / aiortc） | 同上 + 適応・NACK/FEC | 中 | 2 |

4. **heartbeat（値はすべて未凍結・`OQ-OD25`）**:

| 項目 | 候補 | 根拠 |
|---|---|---|
| 周期 | 200 ms（5 Hz） | Guardian 50 ms tick の 4 倍・`/joy` 再生レートと整合 [I] |
| 途絶判定 | 1.0 s | 既存の「1.0 s 鮮度窓ファミリ」（`pose_freshness_timeout` 1.0 s＝[warehouse.base.yaml:21](../../config/warehouse.base.yaml:21)・`emergency_clear_after_s` 1.0 s＝[12:622](../architecture/12-infrastructure-common.md:622)）と同格。`/joy` の 0.6 s より緩いのは LTE ジッタを見込むため [I] |
| **単一ソースの置き場** | **docs 正本 = [05 §3](05-safety-envelope-and-intervention.md) の 1 箇所・実装正本 = `warehouse_interfaces.safety` の定数**（`IDLE_SPEED_EPS` を凍結契約へ昇格した #642 の先例＝[12:644](../architecture/12-infrastructure-common.md:644)） | 本 doc は forward link のみ（複製しない） |

## 4. 計算予算（スパイクゲート先行）

- Orin Nano Super 8 GB（CPU/GPU ユニファイド）で「OAK-D ドライバ ×2 → +YOLO TensorRT（信号・歩行者）→ +cliff detector → +Nav2 + EKF ×2 → +映像エンコード」が同居するかを、[doc23 §7 S1](../architecture/23-perception-and-localization.md:208) と同型の差分測定で**先に測る**（数値を発明しない）。OAK-D は深度を on-device で作るため GPU の固定コストが小さい（[04 §1-2](04-perception-sidewalk-and-signals.md)）。
- 映像はソフトエンコードで CPU を食うため、Nav2 / EKF の CPU 予算と同じ表で管理する。

## 5. OPEN QUESTIONS（接頭辞 `OQ-OD2*`）

- `OQ-OD20` リンク断の判定閾値（秒）と、停止までに現在の区間を安全に終える猶予を許すか（停止そのものは確定）。
- `OQ-OD21` 固定経路の記録形式と保管場所（[03 §3-3](03-localization-gnss-and-ekf.md) の案・走行記録 `mwr-run-record.v0` との同居）。
- `OQ-OD22` 横断承認の UI と承認トークンの有効期限。
- `OQ-OD23` 遠隔操作リンクの package / プロセス境界の凍結（`web_bridge` と別プロセス別 port は確定＝[22:99](../architecture/22-web-observability.md:99)。package 名・port を契約カタログに載せるか）。
- `OQ-OD24` WS チャネルで運ぶ意味の閉集合を凍結し、R-26 相当の AST pin を新 package に課すか。zenoh / rosbridge を採らない理由を ADR 化するか。
- `OQ-OD25` heartbeat 閾値の実装単一ソース（`warehouse_interfaces.safety` の凍結定数）と docs 正本の一本化（05 §3）。
- `OQ-OD26` 映像の Phase 1 コーデックと、**映像断をリンク断 watchdog に含めるか**（映像は法的に操作の前提）。 → **v2.1 の方向（2026-09-14）**: 映像は制御と別接続にし、映像断は heartbeat と別に監視して映像鮮度に応じて遠隔速度上限を下げる（[09 §2-d](09-external-review-v3-response.md)・`OQ-OD90`）。
- `OQ-OD27` teleop 入力の WS 再生経路に deadman 相当をどう作るか（物理スティック中立の観測が遠隔では別意味・`/joy` 鮮度 0.6 s の屋外版）。

## References

- [mode-outdoor/README](README.md) / [00](00-mission-and-scope.md) / [01](01-legal-envelope-japan.md)（§5・追補②）/ [03](03-localization-gnss-and-ekf.md) / [04](04-perception-sidewalk-and-signals.md) / [05](05-safety-envelope-and-intervention.md) / [06](06-hardware-delta-and-base-selection.md)
- [productization/01-commercial-box-map.md:174](../productization/01-commercial-box-map.md:174)（レイヤ対応表）/ [architecture/12-infrastructure-common.md:47](../architecture/12-infrastructure-common.md:47)（時間階層）/ [:622](../architecture/12-infrastructure-common.md:622) / [:644](../architecture/12-infrastructure-common.md:644)（#642 先例）
- [architecture/22-web-observability.md](../architecture/22-web-observability.md)（[:24](../architecture/22-web-observability.md:24) 非ゴール / [:25](../architecture/22-web-observability.md:25) rosbridge 不採用 / [:97](../architecture/22-web-observability.md:97) / [:99](../architecture/22-web-observability.md:99) 別プロセス別 port / [:283](../architecture/22-web-observability.md:283) R-26 / [:369](../architecture/22-web-observability.md:369) 別アプリ）/ [app.py:7-12](../../ws/src/warehouse_web_bridge/warehouse_web_bridge/app.py:7)
- [jetson/02-remote-access-and-dev-link.md:357](../jetson/02-remote-access-and-dev-link.md:357)（Tailscale）/ [jetson/03](../jetson/03-build-deploy-run-and-run-records.md) / [mode-m1/03:49,51,52](../mode-m1/03-joystick-teleop-bringup.md:49) / [config/warehouse.base.yaml:21](../../config/warehouse.base.yaml:21)
- 一次情報（参照日 2026-09-12・[L] は調査レーン報告）: eProsima Fast DDS「Discovery Server」<https://fast-dds.docs.eprosima.com/en/v2.3.1/fastdds/ros2/discovery_server/ros2_discovery_server.html> / NVIDIA Jetson Linux「Software Encode in Orin Nano」<https://docs.nvidia.com/jetson/archives/r36.5.2/DeveloperGuide/SD/Multimedia/SoftwareEncodeInOrinNano.html> / zenoh-plugin-ros2dds <https://github.com/eclipse-zenoh/zenoh-plugin-ros2dds> / rosbridge_suite <https://index.ros.org/p/rosbridge_suite/> / NVIDIA Developer Forums（Orin Nano video encode）<https://forums.developer.nvidia.com/t/orin-nano-video-encoding-support/242135> [D]

## 【2026-09-16 追補】随伴 Mac を分担表へ位置づける（第 4 の段を作らない・随伴フェーズ限定・調査レーン E・裁定待ち = `OQ-OD28`〜`OQ-OD2A`）

正本 = 本 doc §1 の 3 原則。ユーザー指示（2026-09-16）「実際にこの車体を動かす際は Mac（MacBook Pro M4 16GB）と連携しておくので Mac 側のメモリも考慮してよい」を受け、走行時の Mac を分担表に載せる。**§2 の表は行ズレ回避のため触らず、追記案を本追補に置く**（[status-maintenance.md](../../.claude/rules/status-maintenance.md) 末尾追記原則）。知覚側の帰結は [04 末尾追補 §7](04-perception-sidewalk-and-signals.md)。参照日 2026-09-16・[D] = 一次情報実読。

1. **本文は「PC」を役割（遠隔操作者卓）としてしか定義しておらず機種を書いていない**（[:15](02-architecture-split-orin-pc-cloud.md:15) / [:30](02-architecture-split-orin-pc-cloud.md:30)）。一方 [shared/02:134](../shared/02-hardware-design.md:134) は「Mac は開発・シミュレーション専用で本番では使わない」と明記する。走行時に Mac を使うならこの 1 行が唯一の衝突点であり、**本追補は shared/02 を置換せず「随伴フェーズ限定の例外」として additive に扱う**（shared/02 側には同一行注記を置いた）。
2. **法の写像**: [01 §5](01-legal-envelope-japan.md) が禁じるのは「遠隔の人が**操作**できない状態」であって助言計算の喪失ではない。→ **卓としての Mac 断 = リンク断 = 停止（必須）／助言としての Mac 断 = 助言喪失のみで走行継続（適法）**。どちらも成立するが、**同一機に両役を載せない**（[:47](02-architecture-split-orin-pc-cloud.md:47) の「縮退方針が逆向きの 2 機能を 1 プロセスに置かない」を機体レベルへ拡張 = `OQ-OD2A`）。
3. **第 4 の段を作らない**。随伴 Mac は §1 原則 3（クラウド = 無くても走れる助言・記録）の**物理配置違い**として扱う。縮退意味論が一致するため 3 原則の改訂は不要（`OQ-OD28`）。
4. **随伴 Mac は stop producer になれない**（[05 §0](05-safety-envelope-and-intervention.md:11) 許可判断点を新設しない・[05 §3-3](05-safety-envelope-and-intervention.md:61) `speed_limit` 単一 publisher = ADR-0012 決定 11）。返す型は observation / proposal に限り、`/operator/stop_request`・`/safety/stop_request`・`/bot{n}/stop_state`・`/bot{n}/cmd_vel*`・`/bot{n}/speed_limit` の producer にならない（R-26 相当の AST pin で固定できる）。
5. **フェーズ依存**: 随伴通行（[01 §5](01-legal-envelope-japan.md)）では人が車体の横を歩くので Mac は物理的に随伴できる。遠隔操作通行では届出事項「遠隔操作場所の所在地」（[01 §4](01-legal-envelope-japan.md)）に縛られ **Mac は卓側に固定 = 随伴計算にならない** → 助言は GCP Hermes へ戻すか卓に同居させる（`OQ-OD28`）。
6. **帰り道は `web_bridge` ではない**（[:47](02-architecture-split-orin-pc-cloud.md:47)・[doc22:283](../architecture/22-web-observability.md:283) の observe-only R-26 pin）。Orin → Mac のテレメトリ受信だけなら衝突しないが、Mac → Orin に何かを返す瞬間に衝突する。助言専用の第 3 プロセス・第 3 port（`advisory_link_node`・0 actuation・AST pin）を立てる案 vs `operator_link_node` の閉集合拡張（`OQ-OD24` の最小性を損なう）= `OQ-OD29`。
7. **Mac に置いてよい / いけない**:

| 処理 | Mac | 失ったときの挙動 | 根拠 |
|---|---|---|---|
| 停止 producer 全般・Emergency Guardian・地形 / cliff・coverage・信号の fail-closed 観測・X2 判定・横断ゲート・Nav2 / EKF | **✗ Orin 常駐** | — | [:14](02-architecture-split-orin-pc-cloud.md:14) 原則 1・[05 §3-2](05-safety-envelope-and-intervention.md:47)・[04 末尾追補 §2](04-perception-sidewalk-and-signals.md)・[19:139](../architecture/19-environments-and-config.md:139)（Docker-on-Mac は実時間性を近似不可） |
| 10_Evaluation（bag replay・SAM 3 / DINOv3 ラベリング・回帰）| ○（offline・走行中に動かさない）| 影響なし | [04 末尾追補 §3-3](04-perception-sidewalk-and-signals.md) |
| 04_Surface_Semantics 検証器・06 Prediction 比較実装 | ○（best-effort）| 助言喪失のみ | 出力は observation（通行可否を出さない） |
| VLM 助言（Slow-brain 型・Mode X-ER の屋外版）| ○ | 「助言なし」に縮退して走行継続 | [:16](02-architecture-split-orin-pc-cloud.md:16) 原則 3・`UNKNOWN` を `GREEN` に昇格しない |
| 映像記録・run record・Langfuse の受け皿 | ○ | 記録欠落のみ | [:32](02-architecture-split-orin-pc-cloud.md:32) / [jetson/03](../jetson/03-build-deploy-run-and-run-records.md) |
| 遠隔操作者卓 UI | ○ ただし**助言計算と同居させない** | 法的必須層 = heartbeat 途絶で停止 | [:30](02-architecture-split-orin-pc-cloud.md:30) |

8. **実行手段**（[D]）: PyTorch MPS は macOS 14.0+ <https://docs.pytorch.org/docs/2.14/notes/mps.html>／MLX は Apple silicon・macOS ≥ 14.0・native arm64・unified memory <https://ml-explore.github.io/mlx/build/html/install.html>／CoreML export は YOLOv8 / 11 / 26 対応・推論と検証は macOS のみ <https://docs.ultralytics.com/integrations/coreml/>／SAM 3 は 3.45 GB・473.6M（Meta 記事は 848M と不一致 [L]）・重みは HF で要申請・CoreML / MPS の記載なし <https://docs.ultralytics.com/models/sam-3/>／DINOv3 は `dinov3-license`・gated。**16 GB unified 上での同時常駐可否は未実測**。
9. **ROS 2 側だけ塞がっている**: REP-2000 の Humble macOS は Tier 3 かつ amd64 のみ（arm64 の行なし）[D]。repo の dev Docker も [ADR-0008:63](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md:63) の jazzy ドリフトが未解消。→ **随伴 Mac を ROS ノードにせず、WS クライアント + 推論プロセスにする**（§3-2 の「運べる意味を列挙できる」採用理由と一致）。
10. **リンク**: Tailscale が既定（[jetson/02:357](../jetson/02-remote-access-and-dev-link.md:357)・CGNAT / テザリング可・DERP フォールバック [D]）。**RTT・ジッタの実測は repo に無い**（`OQ-OD50` 未決）。推論用ストリームは操作映像と別の 2 本目になり、遠隔時は LTE 上りを二重に食う（随伴時は同一 WiFi なので食わない = 随伴限定の技術的裏付け）。

**§2 分担表への追記案**（裁定後に表末尾へ append する行）:

| 実行場所 | 担当 | 層 | 通信断時 |
|---|---|---|---|
| **随伴 Mac（M4 16GB・随伴フェーズ限定・`OQ-OD28`）** | best-effort 助言計算（VLM / ER の状況説明・06 Prediction 比較・04_Surface 検証器）。出力は observation / proposal 型に限り stop_request・cmd_vel・speed_limit の producer にならない | L4 Non-RT（クラウド段の物理配置違い） | 「助言なし」に縮退して走行継続（Mac 断で停止させない） |
| 〃 | 走行ログ・映像・run record の受け皿（X1 の出先） | 観測面 | 記録欠落のみ |
| 〃 | 10_Evaluation（bag replay・SAM 3 / DINOv3 ラベリング・回帰）は走行中に動かさない | 観測面（offline） | 影響なし |

**OPEN（`OQ-OD2*` の続き）**:

- `OQ-OD28` 随伴 Mac を「クラウド段の物理配置違い」として §2 に載せるか、第 4 の段を新設するか。遠隔操作フェーズでは随伴できないため随伴フェーズ限定資産として扱うか。
- `OQ-OD29` Mac → Orin の帰り道: `operator_link_node` の閉集合拡張か、助言専用の第 3 プロセス・第 3 port（`advisory_link_node`・0 actuation・AST pin）か。`web_bridge` に送信可能な WS を足す選択肢は無い。
- `OQ-OD2A` 随伴 Mac と PC 卓を同一機に同居させない規律の明文化（同居させるなら Mac 断 = リンク断 = 停止になる）。
