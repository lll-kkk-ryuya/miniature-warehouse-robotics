# 02 — Orin / PC / クラウドの分担（architecture split）

作成日: 2026-09-12
Status: **箱（skeleton）**。§2 の分担表は 2026-09-12 所見の**草案（未裁定）**であり、[00 §4](00-mission-and-scope.md) の裁定後に本文へ昇格させる。

> 正本ルート: [mode-outdoor/README](README.md)。レイヤ定義は [productization/01 §レイヤ annotation 対応表](../productization/01-commercial-box-map.md:174)、時間階層（Hard / Soft / Non-RT）は [architecture/12:47](../architecture/12-infrastructure-common.md:47)、環境分離は [architecture/19:16](../architecture/19-environments-and-config.md:16)。

## 0. 位置づけ

屋外走行では「車載（Orin）で閉じるもの」「法的に人が握るもの（PC）」「無くても走れるもの（クラウド）」を最初に分ける。分け方を誤ると、通信断のたびに法の枠（[01 §5](01-legal-envelope-japan.md) 自動操縦の除外）から外れるか、安全でない停止が起きる。

## 1. 分担の 3 原則

1. **通信が切れても安全に止まれる機能は Orin に置く**（自己位置・局所計画・障害物・信号検出・停止 producer・物理非常停止）。
2. **法的に人が握る停止・介入は PC（遠隔操作者卓）に置き、常時接続を前提にする**（監視・非常停止・手動 takeover・横断承認）。PC からの heartbeat が途絶えたら Orin 側が止める（[05 §3](05-safety-envelope-and-intervention.md)）。
3. **無くても走れる助言・記録はクラウドに置く**（LLM / Gemini ER の状況説明・NTRIP 補正・Langfuse。経路は特定の固定経路なので経路計算サービスは不要）。切れたら「助言なし」に縮退して走行を継続する。

## 2. 分担表（草案・未裁定）

| 置き場所 | 機能 | layer / 時間階層 | リンク断時の挙動 |
|---|---|---|---|
| **Orin（車載）** | `navsat_transform` + 2 段 EKF（local: wheel + IMU（+ MOLA-LO）／ global: + GNSS）。map frame は datum 固定の UTM 系（[03](03-localization-gnss-and-ekf.md)） | 帰属未定（暫定 L1 Navigation・EKF / navsat は正準表に行なし） | 自己完結 |
| | Nav2 MPPI + rolling global costmap + waypoint follow（Humble: `fromLL` 変換 + 既存座標 goal） | L1 Navigation | **停止**（リンク断 watchdog が stop_request を engage＝[05 §3](05-safety-envelope-and-intervention.md)。自律継続は法の枠外＝[01 §5](01-legal-envelope-japan.md)） |
| | 屋外 3D 知覚 → 障害物層・歩道走行可能領域層、負障害物（縁石）検出（[04](04-perception-sidewalk-and-signals.md)） | L1 Navigation（costmap 入力） | 自己完結 |
| | 歩行者用信号の検出・状態分類（TensorRT）。結果は perception producer | L4 知覚 | 自己完結 |
| | 横断許可ゲート（青 かつ 点滅なし かつ 遠隔者承認トークン。fail-closed）（[05 §4](05-safety-envelope-and-intervention.md)） | L2 Governance | 承認が来ない = 渡らない |
| | 新規 producer: リンク断 watchdog・GNSS 品質ゲート・ジオフェンス（[05 §3](05-safety-envelope-and-intervention.md)） | L1 Safety | 停止 |
| | 映像の software encode（Orin Nano に HW エンコーダなし＝本 doc References の NVIDIA フォーラム） | 観測面 | 解像度を落として CPU 予算を守る |
| **PC（遠隔操作者卓）** | 映像・地図上位置・状態の監視、非常停止、手動 takeover、横断承認（[architecture/22](../architecture/22-web-observability.md) の console 拡張） | 観測面 + L1 stop producer | **法的必須層**。heartbeat が届かなければ Orin 側が止まる |
| | 固定経路の記録（teach）と再生（repeat）: 随伴 teleop で走った RTK 軌跡から waypoint 列 + 横断ノードを作り、L3 task graph の入力へ投入（汎用経路計画は対象外・ユーザー決定） | L3 入力 | 投入済みミッションは Orin が保持 |
| | rosbag / run record（[jetson/03](../jetson/03-build-deploy-run-and-run-records.md)）。Tailscale over LTE（[jetson/02 §9.7](../jetson/02-remote-access-and-dev-link.md:357)） | 観測面 | 記録欠落のみ |
| **クラウド** | Hermes Gateway（GCP）+ LLM / Gemini ER: 状況説明・例外時の選択肢提示・遠隔者向け要約 | L4 Non-RT | 「助言なし」に縮退して走行継続 |
| | NTRIP 補正配信（外部サービス）、Langfuse・ログ集約 | 観測面 | RTK が float/single に落ちれば GNSS 品質ゲートが減速・停止 |

## 3. 通信（何を書くか）

- `# TODO(設計)` DDS を LTE に流さない（discovery / 帯域）。PC ↔ Orin は web_bridge の WS（既存）か同等の単一チャネルで heartbeat・teleop・映像・テレメトリを運ぶ。Orin 側で `/joy` 相当を再生する node（L4 入力側）。
- `# TODO(設計)` heartbeat 周期・途絶判定閾値（[05 §3](05-safety-envelope-and-intervention.md) と同一の値を単一ソースにする）。
- `# TODO(設計)` 映像の解像度 / fps / codec と LTE 帯域。

## 4. 計算予算（何を書くか）

- `# TODO(spike)` Orin Nano Super 8GB（CPU/GPU ユニファイド）で YOLO・セグメンテーション・ステレオ・Nav2・EKF・映像エンコードが同居するかを、[doc23 §7 S1](../architecture/23-perception-and-localization.md) と同型のスパイクゲートで**先に測る**（数値を発明しない）。

## 5. OPEN QUESTIONS（接頭辞 `OQ-OD2*`）

- `OQ-OD20` リンク断の判定閾値（秒）と、停止までに現在の区間を安全に終える猶予を許すか（停止そのものは確定＝[01 §5](01-legal-envelope-japan.md)・[05 §3](05-safety-envelope-and-intervention.md)）。
- `OQ-OD21` 固定経路の記録形式（緯度経度列 + 横断ノード注記 + 速度帯）と保管場所（走行記録 `mwr-run-record.v0` と同居させるか）。
- `OQ-OD22` 横断承認の UI と、承認トークンの有効期限。

## References

- [mode-outdoor/README](README.md) / [00](00-mission-and-scope.md) / [03](03-localization-gnss-and-ekf.md) / [04](04-perception-sidewalk-and-signals.md) / [05](05-safety-envelope-and-intervention.md) / [06](06-hardware-delta-and-base-selection.md)
- [productization/01-commercial-box-map.md:174](../productization/01-commercial-box-map.md:174)（レイヤ対応表）/ [architecture/12-infrastructure-common.md:47](../architecture/12-infrastructure-common.md:47)（時間階層）
- [architecture/22-web-observability.md](../architecture/22-web-observability.md) / [jetson/02-remote-access-and-dev-link.md:357](../jetson/02-remote-access-and-dev-link.md:357)（Tailscale）/ [jetson/03-build-deploy-run-and-run-records.md](../jetson/03-build-deploy-run-and-run-records.md)
- Jetson Orin Nano の video encode 制約（software・1080p30 で CPU 1–2 コア）: NVIDIA Developer Forums <https://forums.developer.nvidia.com/t/orin-nano-video-encoding-support/242135>（参照日 2026-09-12）
