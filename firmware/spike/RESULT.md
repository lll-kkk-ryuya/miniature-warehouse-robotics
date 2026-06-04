# R-37 spike 結果 — **REPRODUCED（強制）＋ 採用(a) 単一 Agent OK（distinct client_key 前提）**

実行日: 2026-06-04 / ホスト: MacBook Pro M4 16GB, macOS, Docker 29.3.1 (desktop-linux) /
コンテナ: `mwr-uros-spike`（`--memory=6g`）。**ESP32 実機なし**のソフトウェア micro-ROS クライアント検証。

> **このスパイクが示すこと**: ① 同一 `client_key` を**強制**すると R-37 の「pub/sub の片方しか通らない」が
> **再現**する。② **distinct `client_key`** なら **1 つの `micro_ros_agent` で 2 クライアントが双方向に通る**
> （＝[07:242](../../docs/shared/07-research-notes.md) の採用構成 (a)）。
> **示さないこと（＝R-37 を閉じない理由）**: ホスト既定キーは乱数なので**衝突は自然には起きない**＝
> 強制再現である。loopback は **R-43（LaserScan MTU/フラグメンテーション）・実 WiFi のロス/ジッタを検証不能**。
> **最終クローズは Phase 1 実機（ESP32×2・WiFi）** ＋ **ESP32 ファームが distinct client_key を持つことの確認**。

## 環境 / 版数
| 項目 | 値 |
|---|---|
| Image | `tiryoh/ros2-desktop-vnc@sha256:47c24611686a3bc5729676277485fb4040be56dcae64c4e6aa0740890827508d` (ARM64) |
| OS / GCC | Ubuntu 24.04.4 LTS / GCC 13.3.0 |
| ROS 2 | Jazzy（`/opt/ros/jazzy`、underlay） |
| micro_ros_setup | tags/**5.0.2** (0f5651c, branch jazzy) |
| Micro-XRCE-DDS-Client | v2.1.1-rc-28-g83f129a |
| rmw_microxrcedds | a698d4e (jazzy) |
| Agent | Micro-XRCE-DDS-Agent（micro_ros_setup 5.0.2 jazzy `build_agent.sh`）、`udp4 --port 8888 -v6` |
| Client | `minicar_client`（rclc + rmw_microxrcedds、pub `<ns>/hb` + sub `<ns>/cmd`、std_msgs/Int32、10 Hz） |

## 計測結果（`ros2 topic hz` = PUB、host→`/<ns>/cmd` publish 後の client 受信数 = SUB）
| シナリオ | 構成 | bot1 PUB / SUB | bot2 PUB / SUB | 判定 |
|---|---|---|---|---|
| baseline | 1 agent + 1 client | **9.99 Hz / rx 33** | — | ✓ サニティ（pub/sub/namespace 成立） |
| **repro** | 1 agent + 2 client、**同一 key** `0xB0A71001` | 10.0 Hz / **rx 0** | **PUB なし** / rx 32 | **R-37 再現**（各 bot が片方向を喪失） |
| **fixA** | 1 agent + 2 client、**distinct key** `…001`/`…002` | 9.90 Hz / rx 33 | 9.99 Hz / rx 33 | ✓ **単一 Agent で 2 台双方向 OK** |
| fixB | **2 agent**（8888/8889）+ 2 client、distinct key | 10.0 Hz / rx 32 | 9.99 Hz / rx 32 | ✓ 通るが**不要**（fixA で足りる） |

## 決定的証跡（Agent `-v6` ログ）
**repro（同一 client_key 0xB0A71001）** — 2 つ目のクライアントが**同じ session を奪う**:
```
create_client      | create               | client_key: 0xB0A71001, session_id: 0x81
establish_session  | session established   | client_key: 0xB0A71001, address: 127.0.0.1:7874   ← bot1
establish_session  | session re-established| client_key: 0xB0A71001, address: 127.0.0.1:27060  ← bot2 が同一 session を再確立
delete_object_unlock | object deleted      | client_key: 0xB0A71001, object_id: 0x0000         ← entity 破棄
```
→ 結果: bot1 は pub 継続・**sub 受信 0**、bot2 は **pub が DDS に出ず**・sub 受信のみ。両 client が
`rcl_publish` で `rc=1`（soft-fail）。**「pub/sub の片方しか通らない」= R-37 を強制再現**。

**fixA（distinct key 0xB0A71001 / 0xB0A71002）** — **独立した 2 session**（"re-established"/"deleted" は **0 件**）:
```
create_client | client_key: 0xB0A71001, session_id: 0x81 ; session established  127.0.0.1:23242  ← bot1
create_client | client_key: 0xB0A71002, session_id: 0x81 ; session established  127.0.0.1:58240  ← bot2
```
→ 両 bot とも pub 10 Hz・sub rx 33。**単一 Agent が distinct key の 2 client を正しく多重化**。

## メカニズム（確信度）
- **HIGH**: XRCE-DDS の `client_key` は **Agent 側の session 識別子**。同一キーの 2 クライアントは Agent から見て
  **同一 session の再接続**として扱われ（ログ "session re-established"）、先のクライアントの entity が破棄される。
  ホスト既定キーは乱数（`rmw_init.c:114-118` `srand(uxr_nanos()); client_key = rand();`）。
- **採用 (a) の根拠**: distinct key なら 1 Agent で 2 session が独立共存 → fixA が双方向成立。
- **「別ポート（2 Agent）」は不要 / 別問題**: fixB は loopback では通ったが、micro-ROS-Agent **#21 Case 2** /
  **#62**（2 Agent 間の DDS-graph topic 衝突）は WiFi/実機固有で本 loopback では再現せず。**distinct key < 2 Agent** の
  優先順は誤り → doc を「distinct client_key を第一対策」に更新（[07:242](../../docs/shared/07-research-notes.md)）。

## 決定（de-risk アウトプット）
1. **採用構成 = (a) 単一 `micro_ros_agent udp4 --port 8888`**（凍結 launch
   [warehouse-microros-agent.service:21](../../deploy/jetson/systemd/warehouse-microros-agent.service) のまま変更不要）。
2. **必須要件（firmware）**: **ESP32 2 台は distinct な XRCE `client_key` を持つこと**。micro_ros_arduino 既定キーが
   両機で同一/弱 RNG だと R-37 を踏む。Phase 1 で `rmw_uros_options_set_client_key()`（または BOT_ID/MAC 由来）で
   **明示設定**し、起動時にキー差を確認する。→ firmware の Phase 1 TODO（[firmware/README.md](../README.md) / [#116](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/116)）。
3. **フォールバック（保留）**: distinct key で不可なら **USB 有線（serial）**（#21 Case 5 で有効報告）。本 spike では
   serial は ESP32 前提のため未検証（手順のみ）。**2 Agent/別ポートは降格**（不要・別問題）。

## 限界（no-repro ≠ R-37 クローズ）
- **強制再現**である。実機の本当のリスク＝「ESP32 ファームの RNG/キーが実際に衝突するか」は**ホストでは判定不能** → Phase 1。
- **loopback の限界**: R-43（900 点 LaserScan ≈ 3.6 KB/scan vs UDP MTU ~512 B のフラグメンテーション）・WiFi パケットロス/
  ジッタ・テザリング遅延は**未検証**（[07:253](../../docs/shared/07-research-notes.md) R-43 / T3）。
- fixB の `ros2 node list` に重複ノード名の警告が出るが、これは前シナリオの DDS discovery ゴースト（pub/sub の
  hz・rx 計測自体は per-topic で有効）。`rcutils ... truncated` 行は micro-ROS のエラーバッファ起因のログノイズ。

## 再現
```bash
cd firmware/spike
./run_spike.sh setup     # 初回のみ（コンテナ + agent + minicar_client、数分）
./run_spike.sh all       # baseline -> repro -> fixA -> fixB -> report
```
証跡は `logs/`（`*_agent_*.log` = agent -v6、`*_client_*.log` = client stdout、`*_hz_*.txt`、`*_nodes.txt`/`*_topics.txt`）。

## 設計正本 / 関連
- [docs/shared/07-research-notes.md:242](../../docs/shared/07-research-notes.md)（R-37）・`:79`（T5）・`:253`（R-43）— 本結果で更新。
- [deploy/jetson/systemd/warehouse-microros-agent.service:21](../../deploy/jetson/systemd/warehouse-microros-agent.service)（単一 agent・凍結）。
- 上流: micro-ROS-Agent #21（2019 起票・2022 **無修正クローズ**・STM32/NuttX、ESP32 ではない）/ #62 / #235 / ROS Answers Q399001（pablogs の client_key 仮説）。
