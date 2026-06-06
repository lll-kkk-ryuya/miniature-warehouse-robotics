# メモリゲート spike 結果（R-02 / R-38, 段階1 Mac Docker）— **PENDING（未計測 / scaffold）**

実行日: _TBD_ / ホスト: MacBook Pro M4 16GB, macOS, Docker _TBD_ /
コンテナ: `mwr-memgate-spike`（`--memory=6g --memory-swap=6g`）。**Jetson 実機なし**の段階1 近似。

> **結論一行（実測後に記入）**: _「OOM 発火: 有/無 → 残RAM @peak: ___ MiB → 500MB 閾値: 上/下回り
> → R-38（Open-RMF）所見: 段階1 GO 寄り（要段階2）/ No-Go 寄り」_

> **このスパイクが示すこと**: `docker run --memory=6g` で Phase 0.5 **フルスタック**（sim + Nav2×2 +
> AMCL + State Cache + Emergency Guardian + nav2_bridge + LLM Bridge ＋ 外部 Hermes ＋ in-process
> MCP）を起動し、**段階1（早期スモーク）で OOM するか・残RAM 概算**を測る。ここで OOM する設計は実機
> でも確実に落ちる（[doc06:94](../../docs/architecture/06-implementation-phases.md)）。
> **示さないこと（＝R-02/R-38 を閉じない理由）**: ① **段階2（実機 Jetson）は別物**＝ユニファイド
> メモリ（CPU/GPU 8GB 共有）と JetPack 実消費は Mac `--memory` 近似で**出ない**（[doc06:99-101](../../docs/architecture/06-implementation-phases.md)）。
> 最終値は実機 `free -h` 30秒×10分（doc06:96）＝**Phase 1**。② コンテナ内 `free -h` は **HOST RAM**
> （`--memory` cap を無視）＝段階1 の正準残RAM は **cgroup（memory.current/max/peak）＋ docker stats**。
> ③ 本測定に **Open-RMF（Mode C）実体は未搭載**＝Open-RMF 追加分は「残RAM に載る余地」の**所見**のみ
> （最終は段階2＋Open-RMF 実測＝[07:254](../../docs/shared/07-research-notes.md) で R-38 ゲート後に defer）。

## 環境 / 版数（実測後に記入）
| 項目 | 値 |
|---|---|
| Image | `tiryoh/ros2-desktop-vnc:jazzy` @ _sha256:TBD_ (ARM64) |
| Docker | _TBD_ |
| OS | Ubuntu 24.04（tiryoh jazzy）|
| ROS 2 | Jazzy（`/opt/ros/jazzy`）|
| Nav2 | `ros-jazzy-navigation2` _TBD_ |
| gz | Gazebo Sim _TBD_（Harmonic / gz-sim8）|
| Hermes Gateway | _TBD_（無ければ「ROS-only 上限スタック測定＝Hermes 常駐分は別途加算」）|
| ws build | `colcon build` 緑/赤: _TBD_（`logs/setup_build.log`）|

## 計測結果（段階1 — cgroup が正準・`free -h` は参考, `--memory=6g`）
> 取得: `run.sh measure`（30秒×21 サンプル ≈ 10 分, doc06:96 cadence）→ `logs/measure_timeseries.tsv`。

| サンプル | t (s) | cgroup current (MB) | cgroup peak (MB) | docker stats MemUsage | oom_kill |
|---|---|---|---|---|---|
| _TBD_ | 0 | _TBD_ | _TBD_ | _TBD_ | _TBD_ |
| … | … | … | … | … | … |

> 単位は **MB＝10^6**（doc06:98 の "500MB" と揃える。`run.sh report` が同単位で算出）。

**サマリ（`run.sh report`）**
| 量 | 値 | 判定 |
|---|---|---|
| cgroup limit | ≈6442 MB（=6GiB, 10^6）| — |
| ピーク使用量 | _TBD_ MB | — |
| **残RAM @peak**（limit − peak）| _TBD_ MB | **vs 500MB 閾値: _TBD_** |
| **OOM 発火**（cgroup oom_kill 主 / `.State.OOMKilled` / dmesg 副）| _TBD_ | **>0 ＝段階1 FAIL** |
| **Hermes daemon counted / core stack live** | _TBD_ (yes/no) | yes でなければ verdict は FLOOR 扱い |

## フルスタック起動確認（`ros2 node list`, DoD）
> `logs/measure_nodes.txt`。期待: nav2×2（controller/planner/bt_navigator/amcl 各 bot）/ state_cache /
> emergency_guardian / nav2_bridge / llm_bridge。

| ノード | 起動 |
|---|---|
| nav2 (bot1/bot2: controller/planner/bt_navigator/amcl) | _TBD_ |
| state_cache | _TBD_ |
| emergency_guardian | _TBD_ |
| nav2_bridge（Mode A/B のみ）| _TBD_ |
| llm_bridge | _TBD_ |

## R-38（Open-RMF）Go/No-Go 所見（doc06:98 / [07:212,243](../../docs/shared/07-research-notes.md)）
実測後、下記ロジックで一行判定を記入する:
1. **OOM 発火 → 段階1 FAIL**（最優先, doc06:94）。設計を縮退（ヘッドレス徹底/プロセス削減）して再測。
2. **OOM なし ∧ 残RAM < 500MB → No-Go 寄り**: Open-RMF を初回公開から分離／Mode B 格下げ／Open-RMF
   別マシン offload を検討（07:243 の分岐）。`project_release_strategy`（Mode C 初回分離）の数値根拠。
3. **OOM なし ∧ 残RAM ≥ 500MB → 段階1 GO 寄り**: ただし Open-RMF 実体は未測＝**段階2（実機）必須**。
   残RAM が Open-RMF の漸増（解放されない既知問題, 07:243）を吸収できるかは段階2 で確定。

_所見（実測後）_: _TBD_

## 緩和策（所見のみ・実装変更なし）
- **ヘッドレス**: 本 spike は既に PID1=`sleep infinity`＝VNC デスクトップ非起動・gz headless で
  doc06:102 の 0.5-1GB 節約を享受済。実機段階2 でもデスクトップ GUI 無効化を継続する。
- 残RAM が閾値近傍なら: 再測 or プロセス削減（例: 比較検証外では Langfuse/MCP toolset を絞る）。

## 限界（段階1 ≠ R-02/R-38 クローズ）
- Mac `--memory` 近似はユニファイドメモリ/JetPack を再現しない（doc06:99-101）＝**最終判定は段階2 実機**。
- Hermes を縮退（未 install）で測った場合、Hermes Gateway 常駐分（独立 daemon, doc12a:409）は別途加算。
- 本測定は Mode A/B フルスタック＝Open-RMF 実プロセス分は未計上（R-44 で R-38 ゲート後に defer）。

## 再現
```bash
cd spike/memory-gate
./run.sh all       # setup -> run -> measure -> report
# or step-by-step: setup / run / measure / report / clean
```
証跡は `logs/`（`measure_timeseries.tsv`・`measure_oom.txt`・`measure_nodes.txt`・`setup_*.log`）。

## 設計正本 / 関連
- [docs/architecture/06-implementation-phases.md:89-104](../../docs/architecture/06-implementation-phases.md)（二段構え・6GB/500MB/30s の出所）
- [docs/shared/07-research-notes.md:153](../../docs/shared/07-research-notes.md)（R-02）/ `:212`（Action 2 = 500MB 即決）/ `:243`（R-38）/ `:254`（R-44 defer）
- [ws/src/warehouse_bringup/launch/bringup.launch.py:1-79](../../ws/src/warehouse_bringup/launch/bringup.launch.py)（合成構成・非合成境界）
- 先例: [ws/src/warehouse_sim/spike/RESULT.md](../../ws/src/warehouse_sim/spike/RESULT.md)・[firmware/spike/RESULT.md](../../firmware/spike/RESULT.md)
