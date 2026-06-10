# memory-gate spike — Phase 0.5 段階1 全スタックメモリゲート（R-02 / R-38, throwaway probe）

実機（Jetson Orin Nano 8GB）到着前に、**全スタックが Jetson のメモリに収まるか**を Mac Docker で
先行検証する。`docker run --memory=6g --memory-swap=6g` の tiryoh コンテナ内に
`bringup.launch.py` の **Phase 0.5 フルスタック**（sim + Nav2×2 + AMCL + State Cache +
Emergency Guardian + nav2_bridge + LLM Bridge ＋ 外部 Hermes Gateway ＋ in-process Warehouse
MCP）を起動し、**OOM Killer 発火の有無**と**残RAM/ピーク**を計測する。ここは**使い捨ての検証
コード**であり実機能ではない（実装は `ws/src/warehouse_*`、launch は `warehouse_bringup`）。

正本: [docs/architecture/06-implementation-phases.md:89-102](../../docs/architecture/06-implementation-phases.md)
（メモリ検証「二段構え」）/ [docs/shared/07-research-notes.md:153](../../docs/shared/07-research-notes.md)（R-02）
・`:212`（Action 2 = 500MB 判定）・`:243`（R-38）・`:254`（R-44 が R-38 ゲートを参照）。

## なぜ Mac Docker で出来るのか / 何が出来ないのか
- **出来る（段階1 = 早期スモーク）**: Mac M4 も Jetson も ARM64。Docker のメモリ上限を 6GiB に
  絞れば「Jetson 8GB − JetPack(OS+CUDA+デスクトップ) ~2-2.5GB ＝アプリ実質 ~5.5-6GB」を擬似
  再現できる（[doc06:91-93](../../docs/architecture/06-implementation-phases.md)）。**ここで OOM
  する設計は実機でも確実に落ちる**（doc06:94）。
- **出来ない（＝GO でも R-02/R-38 を閉じない理由）**: ① **ユニファイドメモリ**（Jetson は CPU/GPU
  が 8GB を共有。GPU 処理が同じ 8GB を食い合う）は Mac の `--memory` では再現不能。② **JetPack
  実消費**は実機でしか測れない（doc06:99-101）。**最終値は段階2＝実機 `free -h` 30秒×10分**
  （doc06:96）。段階1 は早期警告であって判定そのものではない。
- ③ 本測定は **Mode A/B フルスタック**（`bringup.launch.py`）。**Open-RMF（Mode C）の実プロセスは
  未搭載**＝Open-RMF の追加メモリは「残RAM に載る余地があるか」の**所見**として扱う（R-38 ゲートの
  一次入力。最終は段階2＋Open-RMF 実測 = R-44 で defer, [07:254](../../docs/shared/07-research-notes.md)）。

## 計測の正しさ（段階1 固有の注意 — RESULT に明記する）
- **`free -h` をコンテナ内で見ると HOST の RAM が出る**（`--memory` cap を無視する既知の Docker
  挙動）。よって段階1 の**正準な残RAM 信号は cgroup**（`memory.current` / `memory.max` /
  `memory.peak`）と **`docker stats`（6g 上限に対する MemUsage）**。`free -h` は doc06:96 の体裁
  どおり**参考としてのみ**記録する（実機段階2 では `free -h` が正準）。
- **OOM 検出**: 子プロセス（nav2/gz 等）が cgroup OOM-kill されても `docker inspect
  .State.OOMKilled`（＝PID1=`sleep infinity` のみ追跡）は **true にならない**。よって**主信号は
  cgroup `memory.events` の `oom_kill` カウンタ**（cgroup 内の任意プロセスで増える）。
  `.State.OOMKilled` と `dmesg` は副信号として記録。
- **ヘッドレス**: コンテナ PID1 は `sleep infinity`＝VNC デスクトップを起動しない。gz も
  `-s -r --headless-rendering`。doc06:102 の「ヘッドレスで 0.5-1GB 浮く」を最初から享受。

## 成果物
- `run.sh` — 再実行可能ドライバ（`setup | run | measure | report | all | clean`）。
- `logs/` — 証跡（`measure_timeseries.tsv`・`measure_free.log`・`measure_nodes.txt` /
  `measure_topics.txt`・`measure_oom.txt`・`setup_*.log`・`run_*.log`）。git 追跡外。
- `RESULT.md` — 実測結果と R-38 Go/No-Go 所見（**初版は scaffold＝未計測**。実測後に転記）。

## 手順
```bash
cd spike/memory-gate
./run.sh setup     # 初回：6g コンテナ + Nav2/ros_gz/twist_mux apt + ws colcon build + Hermes（任意）。重い（数十分）
./run.sh run       # bringup.launch.py sim:=true llm:=true を起動（+ Hermes daemon があれば併走）
./run.sh measure   # settle 後、cgroup + docker stats + free -h を 30秒×21 サンプル記録、OOM 確認
./run.sh report    # logs/ を要約（peak / headroom vs 500MB / OOM / ノード存在）→ RESULT へ転記
./run.sh clean     # コンテナ削除
# tunables: MEMGATE_SAMPLES / MEMGATE_INTERVAL / MEMGATE_SETTLE / MEMGATE_MEM
```

## 計測する量と判定基準
| 量 | 取得元 | 判定 |
|---|---|---|
| OOM 発火 | cgroup `memory.events` oom_kill（主）/ `.State.OOMKilled` / dmesg（副） | **>0 ＝段階1 FAIL**（doc06:94 — 実機でも落ちる） |
| ピーク使用量 | cgroup `memory.peak`/`max_usage`（無ければサンプル最大）/ `docker stats` | — |
| 残RAM @peak | `limit − peak`（**MB＝10^6**, doc06:98 の "500MB" と単位を揃える）| **< 500MB ＝Open-RMF(Mode C) No-Go 寄り**（doc06:98 / [07:212](../../docs/shared/07-research-notes.md)）＝**R-38 ゲート抵触**（07:243） |
| フルスタック存在 | `ros2 node list` に nav2×2 / state_cache / emergency_guardian / nav2_bridge / llm_bridge | DoD（起動確認） |

> **R-38 Go/No-Go ロジック**（doc06:98 / 07:212,243）:
> - OOM 発火 → **段階1 FAIL**（最優先）。
> - OOM なし ∧ 残RAM < 500MB → **No-Go 寄り**（Mode C を初回公開から分離／Mode B 格下げ／Open-RMF
>   別マシン offload を検討、07:243）。
> - OOM なし ∧ 残RAM ≥ 500MB → **段階1 GO 寄り**。ただし Open-RMF 実体は未測＝**段階2（実機）必須**。

## セットアップの注意（先行検証メモ）
- **依存 (apt)**: Nav2 実体は `ros-jazzy-navigation2` / `ros-jazzy-nav2-bringup`、sim は
  `ros-jazzy-ros-gz*`、`ros-jazzy-twist-mux`。warehouse_* は `rosdep install` + `colcon build`
  （コンテナ内 `/root/mwr_ws` にコピーしてビルド＝ホスト worktree を汚さない）。ホストは py3.7 で
  ROS/colcon 不可＝tiryoh 必須（`reference_local_gate_execution`）。
- **依存 (pip — apt/rosdep/colcon で入らない)**: `nav2_bridge.py:26` は **eager `import uvicorn`**
  （setup.py の `install_requires` fastapi/uvicorn は colcon が pip-install しない）、`llm_bridge` は
  `langfuse`/`openai`（openai が httpx を同梱）。setup で `pip install fastapi uvicorn langfuse openai`
  を入れる。**入れないと nav2_bridge/llm_bridge が起動時クラッシュ→node list から消え、footprint が
  過小計測**になる（report がクラッシュを検出して警告）。
- **config 解決 (フィデリティ)**: 各ノードは `WAREHOUSE_CONFIG_DIR`（既定は相対 `"config"`,
  `paths.py:56`）から config を読む。`config/` はリポジトリ**ルート**にあり `/root/mwr_ws` には無いので、
  `docker run -e WAREHOUSE_CONFIG_DIR=/repo/config -e WAREHOUSE_ENV=dev` を設定する（repo は ro マウント）。
  **設定しないと `load_config()` が `{}` を返し、`emergency_guardian.py:53` が KeyError でクラッシュ**
  ＝Layer-1 安全ノードが欠落した別物のスタックを測ることになる。
- **Hermes Gateway**: 公式 **git インストーラ**で入れる（`curl -fsSL .../scripts/install.sh | bash`
  → `~/.local/bin/hermes`、`hermes gateway`）。`pipx install hermes-agent` **ではない**
  （[deploy/gcp/README.md:73,86](../../deploy/gcp/README.md)）。setup は **clean な Linux install を先に
  行ってから**ホストの provider キー/config（`~/.hermes` の `.env`/`config.yaml`）を注入する（macOS
  install を再利用すると Linux 用ランチャが生成されず `hermes: command not found` で FLOOR になるため）。
  `run` の liveness は **`:8642` への SERVING プローブ**（`curl` で HTTP 応答 / `ss` で LISTEN を確認）で
  判定し `logs/hermes_present.txt` に記録する。`run_hermes.log` の grep は liveness に**使わない**（bind
  失敗行で false-positive するため）。**install/起動が無ければ Hermes 抜きの「上限スタック」測定に縮退**
  し、その FLOOR は loud に表示される（report の verdict 行は Hermes 未計上時に「FLOOR — NOT a GO」と
  タグされ、`floor_notes` でも「Hermes NOT counted（常駐分を別途加算）」と注記）。
  `MEMGATE_REQUIRE_HERMES=1` を渡すと **run/measure/report が FLOOR を測らず hard-fail（exit 3）**する。
- **Docker Desktop on Mac の Desktop アクセス権**: `docker run -v` のバインドマウントに失敗するなら
  端末（Ghostty 等）の Full Disk Access を確認（`feedback_ghostty_desktop_tcc`）。

## 編集境界
- **本ディレクトリ（`spike/memory-gate/`）のみ自レーン所有**。`bringup.launch.py`・各 `warehouse_*`・
  config・docs 本体は**読んで起動するだけ・編集しない**（他レーン／governance 所有）。docs に反映したい
  知見は PR 本文の「docs 反映 follow-up 提案」に列挙するに留める。

## 設計正本 / 関連
- [docs/architecture/06-implementation-phases.md:89-102](../../docs/architecture/06-implementation-phases.md)（二段構え・6GB/500MB/30s の出所）
- [docs/shared/07-research-notes.md:153](../../docs/shared/07-research-notes.md)（R-02）/ `:212`（Action 2）/ `:243`（R-38）/ `:254`（R-44 defer）
- [ws/src/warehouse_bringup/launch/bringup.launch.py:1-79](../../ws/src/warehouse_bringup/launch/bringup.launch.py)（合成構成・非合成境界＝MCP in-process / Hermes 外部 / micro-ROS は Phase1）
- 先例: [ws/src/warehouse_sim/spike/](../../ws/src/warehouse_sim/spike/)（環境スパイク）・[firmware/spike/](../../firmware/spike/)（R-37）
