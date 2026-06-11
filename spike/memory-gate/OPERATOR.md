# オペレータ GO シート — 段階1 メモリゲート（#187 keystone / R-02・R-38）

> **これは実行中に開いておく1枚カード**。詳細・背景は [README.md](README.md)、結果転記先は
> [RESULT.md](RESULT.md)、判定ロジックの実体は `run.sh`（`compute_verdict_awk` / `verdict_line`）。
> 正本: [doc06:89-102](../../docs/architecture/06-implementation-phases.md) / [07:243](../../docs/shared/07-research-notes.md)（R-38）。
>
> **実行中に見るのは §0–§2（≈1画面）**。終わったら §3 で転記、詰まったら §4。
> **実行は人間（Mac M4・Docker・合計 ~50–70分）**。harness は完成・hardened 済（#201/#210/#217）。

---

## 0. 前提チェック（実行前・30秒）
- [ ] **Docker Desktop 起動中** — `docker version` が版を返す（本確認機 = Server 29.3.1。実値は §3 で `docker version` から転記）。
- [ ] **安定したインターネット接続** — 初回 setup で image `docker pull` ＋ `apt`（Nav2/ros_gz/twist_mux）＋ GitHub からの Hermes git install が走る（テザリングは切断に注意）。
- [ ] **Docker 用ディスク空き ≳15GB** — フルデスクトップ image ＋ apt ＋ `/root/mwr_ws` colcon build 分。不足だと setup が `no space` で中断する。
- [ ] **`~/.hermes/.env` がある** — provider キー＝Hermes 常駐分を計上する前提（確認済）。
- [ ] **端末に Full Disk Access** — 無いと bind mount が失敗する（`feedback_ghostty_desktop_tcc`）。
- [ ] `cd spike/memory-gate` にいる。

## 1. 実行（4ステップ・順に）
`MEMGATE_REQUIRE_HERMES=1` を付ける＝**Hermes 未計上の FLOOR を測らせない**（GO の3条件の1つ）。

```bash
cd spike/memory-gate
export MEMGATE_REQUIRE_HERMES=1            # FLOOR を hard-fail（exit 3）。GO を出すための前提

./run.sh setup     # ~30–60分: 6g container + apt(Nav2/gz/twist_mux) + colcon build + Hermes install
                   #   ✅ 最後に "hermes installed OK (...)" を確認。colcon は失敗時のみ "colcon build FAILED:" を出す（無ければ緑）
                   #   ⛔ "HERMES NOT INSTALLED -> measure will be a FLOOR run" が出たら STOP → §4

./run.sh run       # bringup.launch.py sim:=true llm:=true 起動
                   #   ✅ "hermes daemon LIVE on :8642" を確認
                   #   ⛔ "REFUSED: ... refusing to measure/report a FLOOR" で止まったら → §4

./run.sh measure   # ~10–12分(起動待ち最大2分 + 21×30s): core nodes 起動待ち → cgroup/docker stats/oom を21サンプル
                   #   "core nodes up after Ns" が出れば stack live

./run.sh report | tee logs/report.txt     # 要約＋判定行を表示・保存（転記の元）
```

## 2. 判定（`report` の判定行を上から読む）
**`report` は2行の判定を出す。上から順に**:

1. **`VERDICT: FLOOR — NOT a GO ...`** が出ていたら → **FLOOR**（Hermes 未計上＝Mode A/B 過小計測）。
   `MEMGATE_REQUIRE_HERMES=1` なら通常ここに来ず、`run`/`measure`/`report` が先に `REFUSED ...` で
   exit 3 する。出たら GO 不可 → §4 で Hermes を直して再 setup。

2. **`VERDICT (R-38): ...`** ＝本体判定:

| `report` の文言 | 意味 | 判定 |
|---|---|---|
| `OOM OBSERVED => 段階1 FAIL` | OOM 発火（最優先） | **FAIL** — 実機でも落ちる(doc06:94)。設計縮退して再測 |
| `headroom < 500MB => ... No-Go-leaning` | 残RAM<500MB | **No-Go 寄り**(07:243) |
| `... headroom >= 500MB => 段階1 GO-leaning` | OOM無・残RAM≥500MB | **GO 寄り**（要段階2。Open-RMF 実体は未測） |
| `OOM UNKNOWN ...` | cgroup v1 等で oom counter 不可 | `logs/measure_oom.txt` 確認後に判断 |
| `INVALID ...` | cgroup 計測不能 | `./run.sh measure` 再実行 |

> **GO の3条件（すべて満たす）**: **OOM 無 ∧ 残RAM ≥ 500MB ∧ Hermes counted（＝FLOOR 行が出ていない）**。
> - **GO** → **#180 rmf-adapter（Mode C 本実装）** + **#221 governance** を解錠。
> - **No-Go / FAIL** → **Mode B 格下げ**（doc07:243）。Mode C は初回公開から分離（`project_release_strategy`）。
>
> ⚠️ GO でも R-02/R-38 は**閉じない**: ユニファイドメモリ／JetPack 実消費は Mac で出ない＝
> **最終判定は段階2＝実機 Jetson `free -h` 30s×10min**（doc06:96-101）。段階1 は早期スモーク。

---

## 3. 転記（`logs/report.txt` → [RESULT.md](RESULT.md) の `_TBD_` を埋める・機械的）
`report` 出力が要約済みの正準ビュー。下表のとおり対応づけて転記し、最後に `/consistency-audit`。

| RESULT.md 欄 | 取得元 |
|---|---|
| 結論一行 (L6) | `report` の `VERDICT (R-38)` 行（FLOOR 行が出ていればそれも） |
| ピーク使用量 / 残RAM @peak | `report` の `peak usage` / `headroom @peak` 行 |
| OOM 発火 | `report` の `cgroup oom_kill` 行 ＋ `logs/measure_oom.txt`（副信号） |
| Hermes counted / core stack live | `report` 冒頭 `hermes daemon counted: ... / core stack live: ...` |
| フルスタック node 表 | `report` の `full-stack node presence` 各行（`controller_server` 等 N/期待） |
| サンプル表 | `logs/measure_timeseries.tsv`（MB ＝ bytes ÷ 10^6） |
| Image digest | `docker image inspect tiryoh/ros2-desktop-vnc:jazzy --format '{{index .RepoDigests 0}}'` |
| Docker 版 | `docker version --format '{{.Server.Version}}'` |
| 版数(gz/nav2/py/hermes) | `cat logs/setup_versions.txt` |

## 4. トラブル / 詰まったら
- **`HERMES NOT INSTALLED` / `REFUSED ... FLOOR`**: `cat logs/setup_hermes.log` で原因確認 → `~/.hermes/.env`
  があることを確認 → `./run.sh clean && ./run.sh setup`（clean Linux install をやり直す）。
- **`report` に `run_bringup.log shows a NODE FAILURE`**: pip 依存欠落の疑い → `cat logs/run_bringup.log`、
  `./run.sh setup` 再実行（fastapi/uvicorn/langfuse/openai を入れ直す）。node が過小だと footprint も過小。
- **bind mount 失敗**: 端末の Full Disk Access（`feedback_ghostty_desktop_tcc`）。
- **やり直し**: `./run.sh clean` でコンテナ削除 → `setup` から。証跡は `logs/` に残る（git 追跡外）。
- **オフライン健全性のみ確認**: `./run.sh selftest`（docker 不要・判定分岐の自己テスト）。

## 参照
- 手順詳細・「何が出来て何が出来ないか」: [README.md](README.md)
- 判定ロジック実体: `run.sh` の `compute_verdict_awk`（R-38 分岐）/ `verdict_line`（FLOOR タグ）/ `require_hermes_or_refuse`
- 結果ドキュメント: [RESULT.md](RESULT.md)
- 正本: [doc06:89-102](../../docs/architecture/06-implementation-phases.md)（二段構え）/ [07:153](../../docs/shared/07-research-notes.md)(R-02)・`:212`(500MB即決)・`:243`(R-38)
