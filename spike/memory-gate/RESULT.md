# メモリゲート spike 結果（R-02 / R-38, 段階1 Mac Docker）— **実測済（2026-06-13・段階1 GO-leaning〔条件付き〕）**

> 実行手順・判定・**転記マップ**は [OPERATOR.md](OPERATOR.md)（1枚カード）を参照。本ファイルは実測転記先。

実行日: **2026-06-13** / ホスト: MacBook Pro M4 16GB, macOS, Docker **29.3.1**（host VM 7.65 GiB / cgroup v2 / 10 CPU）/
コンテナ: `mwr-memgate-spike`（`--memory=6g --memory-swap=6g`）。**Jetson 実機なし**の段階1 近似。

> **結論一行**: **OOM 発火: 無**（`oom_kill=0` / `.State.OOMKilled=false` / dmesg 空・21 サンプル×10分）→
> **残RAM @peak（doc 準拠 = `free -h` available 相当 = working-set 基準）: ≈ 2.9–3.1 GiB**（非再利用 `anon`
> のみの保守下限でも ≈ 4.5 GiB）→ **500MB 閾値: 大きく上回り** → **R-38（Open-RMF）所見: 段階1 GO 寄り（要段階2）**。
> ⚠️ harness の生 `headroom @peak = −1 MB`（= `limit − cgroup memory.peak`）は **page-cache を used に算入する計測アーティファクト**で
> あり、これに基づく自動 `VERDICT: No-Go-leaning` は**採用しない**（根拠は §「harness 計測法の所見」）。GO 4条件すべて充足。

> **このスパイクが示すこと**: `docker run --memory=6g` で Phase 0.5 **フルスタック**（sim + Nav2×2 +
> AMCL + State Cache + Emergency Guardian + nav2_bridge + LLM Bridge ＋ 外部 Hermes ＋ in-process
> MCP）を起動し、**段階1（早期スモーク）で OOM するか・残RAM 概算**を測る。ここで OOM する設計は実機
> でも確実に落ちる（[doc06:94](../../docs/architecture/06-implementation-phases.md)）。
> **示さないこと（＝R-02/R-38 を閉じない理由）**: ① **段階2（実機 Jetson）は別物**＝ユニファイド
> メモリ（CPU/GPU 8GB 共有）と JetPack 実消費は Mac `--memory` 近似で**出ない**（[doc06:99-101](../../docs/architecture/06-implementation-phases.md)）。
> 最終値は実機 `free -h` 30秒×10分（doc06:96）＝**Phase 1**。② コンテナ内 `free -h` は **HOST RAM**
> （`--memory` cap を無視）＝段階1 では cgroup を読む。**ただし cgroup `memory.current/peak` は再利用可能な
> page cache を含む usage 値**であり、doc06:96 の `free -h` available（= 再利用可能 cache を available に算入）
> に忠実な「残RAM」は **working set（`memory.current − inactive_file`・`docker stats` と一致）** で測る（§harness 所見）。
> ③ 本測定に **Open-RMF（Mode C）実体は未搭載**＝Open-RMF 追加分は「残RAM に載る余地」の**所見**のみ
> （最終は段階2＋Open-RMF 実測＝[07:254](../../docs/shared/07-research-notes.md) で R-38 ゲート後に defer）。

## 環境 / 版数（2026-06-13 `./run.sh setup` の `logs/setup_versions.txt` 由来）
> 版数は環境固定値でライブ計測（peak/headroom/OOM）とは独立。Docker 版・image digest は実行ホスト固有。
| 項目 | 値 |
|---|---|
| Image | `tiryoh/ros2-desktop-vnc:jazzy`（ARM64）@ `sha256:47c24611686a3bc5729676277485fb4040be56dcae64c4e6aa0740890827508d` |
| Docker | **29.3.1**（Server・cgroup v2・host VM 7.65 GiB ≥ 6 GiB cap → container が cap を満たす）|
| OS | **Ubuntu 24.04.4 LTS**（tiryoh jazzy）|
| ROS 2 | Jazzy（`/opt/ros/jazzy`）|
| Nav2 | `ros-jazzy-navigation2` **1.3.11** |
| gz | **Gazebo Sim 8.11.0**（Harmonic / gz-sim8）|
| Python | **3.12.3** |
| Hermes Gateway | **Hermes Agent v0.16.0 (2026.6.5) · upstream 202e318c**。**計上済（FLOOR ではない）**＝daemon LIVE on :8642（`hermes_present=yes`）。clean Linux install（`logs/hermes_install.txt=installed`）。⚠️ liveness-timing 対応として **コンテナ内 `/root/.hermes/config.yaml` の `mcp_servers` を空に**した（host `~/.hermes` 由来の dead `audio_reviewer`＝macOS 専用パスで Linux に不在・接続失敗で 0 RAM・gateway の :8642 bind を ~7s 遅らせ run.sh の 6s probe を取りこぼしていた）。**footprint-neutral**（接続実績ゼロ＋Warehouse MCP は llm_bridge in-process＝doc15:50）。host 設定は不変 |
| ws build | **緑**（`colcon build`: **13 packages** finished・`logs/setup_build.log`。#219 で `warehouse_rmf_adapter` 追加のため 12→13）|

## 計測結果（段階1 — `--memory=6g`・cgroup v2・21 サンプル×30s ≈ 10 分）
> 取得: `run.sh measure`（doc06:96 cadence）→ `logs/measure_timeseries.tsv`。**MB＝10^6**（doc06:98 の "500MB" と揃える）。

| サンプル | t (s) | cgroup current (MB) | cgroup peak (MB) | docker stats MemUsage | oom_kill |
|---|---|---|---|---|---|
| 1 | 0 | 6370 | 6443 | 3.29 GiB/6GiB | 0 |
| 11 | 300 | 6376 | 6443 | 3.295 GiB/6GiB | 0 |
| 21 | 600 | 6382 | 6443 | 3.30 GiB/6GiB | 0 |

> 全 21 サンプルで `oom_kill=0`、cgroup current は 6370→6382 MB で安定（cap=6442 MB に張り付き）。
> docker stats（= working set = `current − inactive_file`）は 3.29–3.30 GiB＝cap の約 55%。全行は `logs/measure_timeseries.tsv`。

### cgroup memory.stat 内訳（計測直後・stack live）— **cap 張り付きの正体 = page cache**
| 区分 | bytes | MB(10^6) | GiB | 性質 |
|---|---|---|---|---|
| `anon`（プロセス RSS） | 1,800,765,440 | 1801 | 1.68 | **非再利用**（真の working set 中核）|
| `file`（page cache） | 3,950,477,312 | 3950 | 3.68 | **再利用可能** |
| ├ `inactive_file` | 2,838,687,744 | 2839 | 2.64 | 即時 reclaim 可 |
| └ `active_file` | 1,073,156,096 | 1073 | 1.00 | 圧力時 reclaim 可（hot コード/地図ページ） |
| `kernel` | 632,836,096 | 633 | 0.59 | — |
| `slab` | 593,651,056 | 594 | 0.55 | — |

`memory.events`: `low 0 / high 0 / **max 2685** / oom 0 / **oom_kill 0** / oom_group_kill 0`。
→ cgroup は cap(6 GiB) に **2685 回到達し、その都度 page cache を reclaim して 1 件も OOM-kill せず**＝
「cache を貯めているが枯渇していない」健全な v2 挙動の典型（reclaim が headroom である証左）。

**サマリ（`run.sh report` 生出力 + doc 準拠の補正）**
| 量 | 値 | 判定 |
|---|---|---|
| cgroup limit（memory.max）| 6442 MB（=6GiB, 10^6）| — |
| cgroup peak usage（`run.sh` の peak）| 6443 MB | cap 到達（page cache 込み） |
| **残RAM @peak（harness 生 = limit − cgroup_peak）** | **−1 MB** | ⚠️ **page-cache アーティファクト（採用しない）**。§harness 所見 |
| **残RAM @peak（doc 準拠 = limit − working_set）** | **≈ 2.9–3.1 GiB**（6442 − ~3300〔docker stats〕。cgroup 由来 `current−inactive_file`=3543 MB なら ≈ 2.9 GiB）| **≥ 500MB 閾値: 大きく上回り** |
| 残RAM @peak（非再利用 anon のみの保守下限 = limit − anon）| ≈ 4641 MB（4.5 GiB）| ≥ 500MB |
| **OOM 発火**（cgroup oom_kill 主 / `.State.OOMKilled` / dmesg 副）| **0 / false / 空** | **無 → 段階1 PASS**（doc06:92,94 の一次判定）|
| **Hermes daemon counted / core stack live** | **yes / yes** | FLOOR ではない |

## フルスタック起動確認（`ros2 node list`, DoD — `logs/measure_nodes_end.txt`）
| ノード | 期待 | 実測 |
|---|---|---|
| controller_server（bot1/bot2）| 2 | **2/2** |
| planner_server（bot1/bot2）| 2 | **2/2** |
| bt_navigator（bot1/bot2）| 2 | **2/2** |
| amcl（bot1/bot2）| 2 | **2/2** |
| state_cache | 1 | **1/1** |
| emergency_guardian | 1 | **1/1** |
| nav2_bridge | 1 | **3/1**（grep 過大カウント・存在は確認）|
| llm_bridge | 1 | **1/1** |
| 外部 Hermes daemon（:8642）| — | **LIVE（計上）** |

## R-38（Open-RMF）Go/No-Go 所見（doc06:98 / [07:212,243](../../docs/shared/07-research-notes.md)）
判定ロジック（実測あてはめ）:
1. **OOM 発火 → 段階1 FAIL**（最優先, doc06:94）→ **該当せず**（oom_kill=0）。
2. **OOM なし ∧ 残RAM < 500MB → No-Go 寄り** → **該当せず**（doc 準拠 残RAM ≈ 2.9–3.1 GiB ≫ 500MB）。
3. **OOM なし ∧ 残RAM ≥ 500MB → 段階1 GO 寄り**（ただし Open-RMF 実体未測＝**段階2 必須**）→ **該当**。

_所見（実測後）_: **段階1 = GO-leaning（条件付き・非確定）**。`--memory=6g` フルスタック＋常駐 Hermes で
10 分間 **OOM ゼロ**（doc06:92,94 の一次判定クリア）。doc06:96 の `free -h` available 定義に忠実な
残RAM（working-set 基準）≈ **2.9–3.1 GiB**（anon のみ保守下限でも ≈ 4.5 GiB）で **500MB フロア**
（doc06:98 / 07:212）を大きく上回る。**GO 4条件（OOM 無 ∧ 残RAM≥500MB ∧ Hermes counted ∧ core stack live）すべて充足**。
→ OPERATOR.md GO シートに従い **#180（Mode C rmf-adapter 本実装）/ #221 を解錠**。
ただしこれは**早期スモークのみ**で R-02/R-38 は閉じない（下記「限界」「未決」）。

### ⚠️ harness 計測法の所見（`run.sh` の headroom 指標は doc の残RAM 定義に不忠実）
- `run.sh:132-135` は `peak = max(memory.peak, max memory.current)` / `headroom = memory.max − peak`。
  `memory.current`/`memory.peak` は **再利用可能 page cache を含む usage 値**（本測定で `file`=3.68 GiB）であり、
  `limit − peak ≈ −1 MB` は「**cache が cap に触れたか**」を測るだけ（cache-active な負荷では恒常的に ≈0）で、
  実働メモリ余裕とは無関係。doc06:96 の `free -h` の "available" は**再利用可能 cache を used から除外**するため、
  doc 準拠の残RAM は **working set（`memory.current − inactive_file`・`docker stats` が算出する 3.29 GiB）**で測るべき。
- `run.sh:19-22` の cgroup 採用判断は「コンテナ内 `free -h` が HOST RAM を返す」scoping 問題への正しい対処だが、
  そこから **`memory.peak` を引く**のは semantic error（usage 指標 ≠ availability 指標）。
- **OOM 信号（`run.sh:143` の `oom_kill` 主判定）は正しく authoritative**＝そこは PASS。誤射するのは副次の headroom 行のみ。
- **推奨 fix（follow-up・本 PR 範囲外＝harness 挙動変更のため別途）**: headroom/残RAM を
  `memory.current − inactive_file`（working set / `free -h` available 相当）で算出する行を追加し、自動 verdict 行に反映する。

## 未決・暫定・disclose（隠さない — docs-first）
1. **段階2（実機 Jetson）必須**（doc06:95-101）。段階1 は早期警告のみ＝GO-leaning は**条件付き・非確定**。
2. **ユニファイドメモリ非再現**（doc06:99-101）: Jetson は CPU/GPU が 8GB 共有。GPU costmap バースト時の
   cache reclaim/thrash は Mac で出ない＝**段階2 Jetson `free -h` で確定**。`active_file`=1.00 GiB は hot ページで
   全量が無痛 evict ではない点が唯一の実在懸念（段階2 の入力）。
3. **Open-RMF（Mode C）footprint 未計上**: 本 run に Open-RMF プロセスはゼロ。07:243 は fleet_adapter/
   traffic_schedule の **メモリ漸増（解放されない既知問題）** を記録＝R-38 はこの run では閉じない。
   → 段階2 ＋ Open-RMF 実測（R-38 ゲート後）。`malloc_trim`/cache 上限を最初から導入（07:243 緩和策）。
4. **footprint-neutral だが機能未行使の caveat**（residency は確認・計上済）:
   - `llm_bridge` が Hermes へ **401（Invalid API key）→ Nav2-only fallback**＝LLM 推論は未行使（プロセスは常駐・計上）。
   - **Langfuse 4.x API 不整合**（`create_trace_id`/`start_as_current_span` 無し）＝tracing 無効（client は load 済）。
   - → **LLM 推論・tracing の working set は本測定で未行使**。GO を過大解釈しないこと。
   - コンテナ内 `mcp_servers` 空化（上記 Hermes 行）＝footprint-neutral・host 設定不変。

## 緩和策（所見のみ・実装変更なし）
- **ヘッドレス**: 本 spike は既に PID1=`sleep infinity`＝VNC デスクトップ非起動・gz headless で
  doc06:102 の 0.5-1GB 節約を享受済。実機段階2 でもデスクトップ GUI 無効化を継続する。
- 残RAM が閾値近傍なら: 再測 or プロセス削減（例: 比較検証外では Langfuse/MCP toolset を絞る）。

## 限界（段階1 ≠ R-02/R-38 クローズ）
- Mac `--memory` 近似はユニファイドメモリ/JetPack を再現しない（doc06:99-101）＝**最終判定は段階2 実機**。
- 本測定は Mode A/B フルスタック＝Open-RMF 実プロセス分は未計上（R-44 で R-38 ゲート後に defer）。
- 残RAM は doc06:96 の `free -h` available 相当（working set）で評価。cgroup `memory.peak` 生値は cache 込みで過大。

## 再現
```bash
cd spike/memory-gate
./run.sh selftest  # OFFLINE: verdict awk 5分岐 + FLOOR 注記の自己テスト（docker/network 不要）
MEMGATE_REQUIRE_HERMES=1 ./run.sh all   # setup -> run -> measure -> report（要 Docker・重い・実測は人間ゲート）
# or step-by-step: setup / run / measure / report / clean
# 注: Hermes が dead MCP で :8642 bind を 6s 超過すると FLOOR 誤判定 → コンテナ内 config の mcp_servers を空に
```
証跡は `logs/`（`measure_timeseries.tsv`・`measure_oom.txt`・`measure_nodes_end.txt`・`setup_*.log`・`hermes_install.txt`・`report.txt`）。

## 設計正本 / 関連
- [docs/architecture/06-implementation-phases.md:89-102](../../docs/architecture/06-implementation-phases.md)（二段構え・6GB/500MB/30s の出所・段階1=OOM スモーク）
- [docs/shared/07-research-notes.md:153](../../docs/shared/07-research-notes.md)（R-02）/ `:212`（Action 2 = 500MB 即決・Jetson `free -h`）/ `:243`（R-38・Open-RMF 漸増）/ `:254`（R-44 defer）
- [ws/src/warehouse_bringup/launch/bringup.launch.py:1-79](../../ws/src/warehouse_bringup/launch/bringup.launch.py)（合成構成・非合成境界）
- 先例: [ws/src/warehouse_sim/spike/RESULT.md](../../ws/src/warehouse_sim/spike/RESULT.md)・[firmware/spike/RESULT.md](../../firmware/spike/RESULT.md)
