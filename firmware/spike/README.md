# R-37 spike — micro-ROS Agent 多重接続検証（throwaway probe）

実機（ESP32×2）到着前に、**1 つの micro-ROS Agent に 2 台を UDP 接続すると pub/sub の片方しか
通らない**という既知不具合 **R-37**（[docs/shared/07-research-notes.md:242](../../docs/shared/07-research-notes.md)）の
根幹メカニズムを、**ソフトウェア micro-ROS クライアント**で先行検証する。ここは**使い捨ての検証コード**で
あり実機能ではない（実機ファームは `firmware/src/`）。

`tiryoh/ros2-desktop-vnc:jazzy`（ARM64）コンテナ内に、凍結 launch（[warehouse-microros-agent.service:21](../../deploy/jetson/systemd/warehouse-microros-agent.service)）と
同じ `micro_ros_agent udp4 --port 8888` を立て、`minicar_client`（rclc + rmw_microxrcedds の
ホストプロセス）を 2 つ走らせて 4 シナリオを比較する。

## なぜ「ソフトクライアント」で出来るのか / 何が出来ないのか
- R-37 の中核は **XRCE-DDS セッション（client_key）の衝突**仮説（ROS Answers Q399001, pablogs）。
  ホスト既定の client_key は乱数（`rmw_init.c:114-118` `srand(uxr_nanos()); client_key = rand();`）で、
  実クライアント 2 つは**自然には異なるキー**になる → **衝突は意図的に強制しないと再現しない**。
- よって本 spike は **「distinct key なら単一 Agent で 2 台同時に通る（＝修正の検証）」**と
  **「同一 key を強制すると壊れるか（＝故障メカニズムの観察）」**を確かめる。**no-repro は R-37 のクローズではない**。
- loopback UDP なので **R-43（LaserScan の MTU/フラグメンテーション）と実 WiFi のロス/ジッタは検証不能**。
  最終クローズは Phase 1 の実機 2 台 WiFi テスト（+ ESP32 ファームが distinct client_key を持つことの確認）。

## 成果物
- `uros_app/minicar_client/` — パラメタライズド rclc クライアント（pub `<ns>/hb` + sub `<ns>/cmd`）。
  引数: `<agent_ip> <agent_port> <namespace> <client_key_hex> [domain]`。namespace と client_key を
  CLI で振れるので、衝突の強制/回避を 1 バイナリで切替できる。
- `run_spike.sh` — 再実行可能ドライバ（setup / baseline / repro / fixA / fixB / all / report / clean）。
- `logs/` — 証跡（agent -v6 ログ・node/topic list・`ros2 topic hz`・クライアント stdout）。git 追跡外。
- `RESULT.md` — 実測結果と判定。

## 手順
```bash
cd firmware/spike
./run_spike.sh setup      # 初回のみ：コンテナ + agent ws + client ws + minicar_client ビルド（数分）
./run_spike.sh all        # baseline -> repro -> fixA -> fixB -> report
./run_spike.sh report     # logs/ を表に要約
./run_spike.sh clean      # コンテナ削除
```

## シナリオ（マトリクス）
| # | name | 構成 | 狙い |
|---|------|------|------|
| 1 | `baseline` | 1 agent + 1 client | サニティ（graph 可視・pub/sub が成立すること） |
| 2 | `repro` | 1 agent + 2 client、**同一 client_key** | R-37 の故障を**強制再現**（session 衝突の観察） |
| 3 | `fixA` | 1 agent + 2 client、**distinct key + ns** | **単一 Agent で 2 台 OK** か（＝採用候補 a の検証） |
| 4 | `fixB` | **2 agent（別ポート）** + 2 client | doc の「別ポート」案。#21 Case2 / #62 では DDS 層で別問題 |

各シナリオの計測:
- **PUB 試験**: `ros2 topic hz /botN/hb`（agent→DDS に出ているか）。
- **SUB 試験**: `ros2 topic pub /botN/cmd` → クライアント stdout に `SUB cmd=...` が出るか（DDS→agent→client）。
- agent `-v6` ログで `create_client` / `session established` / 同一 key の reset/replace を確認。

## 判定基準
- **採用 (a) 単一 Agent OK**: `fixA` で bot1/bot2 **両方の pub と sub が双方向**に通る（hz 安定・SUB 受信あり）。
  実機要件＝**両 ESP32 が distinct client_key を持つこと**（[07:242](../../docs/shared/07-research-notes.md) 更新参照）。
- **`repro` で片方向落ちを観測**できれば、R-37 の「client_key 衝突」メカニズムを裏づけ（証跡を RESULT に添付）。
- **`fixB` の位置づけ**: `fixA` が通れば 2 agent は不要。`fixB` で topic 衝突が出れば micro-ROS-Agent #62 を裏づけ。

## Jazzy/24.04 ビルド回避メモ（重要）
`build_firmware.sh`（host）は Ubuntu 24.04 / 新しい GCC で 2 点破綻するため**使わない**:
1. `rmw_microxrcedds` が `-Werror`（`cc1: all warnings being treated as errors`）→ `-DCMAKE_C_FLAGS=-w` で無害化。
2. `std_srvs`/`example_interfaces` の service typesupport が `undefined ...service_msgs__msg__ServiceEventInfo`
   （service introspection 未対応）→ pub/sub のみの本 spike では `--packages-skip` で回避。

代わりに micro_ros_setup の host `build.sh` と**同じ 3 フェーズ**で必要分のみビルドする:
microxrcedds typesupport generator をビルド → **`source install/local_setup.bash`** → メッセージ pkg を
ビルド（これで `*__rosidl_typesupport_microxrcedds_c.so` が生成される）。順序を誤ると runtime で
`typesupport identifier (rosidl_typesupport_c) is not supported by this library` になる。詳細は `run_spike.sh` の `setup`。
