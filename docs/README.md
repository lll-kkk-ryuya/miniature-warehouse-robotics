# ドキュメントマップ

> 📍 **プロジェクト現況**は [STATUS.md](STATUS.md)（随時更新の living doc）。
> 📖 **用語の正本**は [GLOSSARY.md](GLOSSARY.md)（docs-authoring 単語帳：各語の 1 行定義＋検証済み canonical anchor。doc/Issue/PR を書く前に参照）。

## 構成

```
docs/
├── shared/          モード非依存（プロジェクト概要・ハードウェア・予算等）
├── architecture/    共通基盤設計（LLM Bridge共通・インフラ・フェーズ計画）
├── productization/  商用再利用 Box 設計（L4/L3/下位ROS/安全/evalの独立部品化）
├── dev/             開発プロセス（並列開発 playbook・オペレーター手順・教訓ログ）
├── setup/           デプロイ手順（Jetson prod 常駐化・systemd / 監視）
├── jetson/          Jetson 忠実度ギャップ・実機投入前ゲート（dev/stg→prod de-risk・#127）
├── mode-a/          Mode A/B: LLM単独交通管理（Open-RMFなし）
├── mode-c/          Mode C: LLM + Open-RMF（主方針）
├── mode-x-er/       Mode X-ER: Gemini Robotics-ER 視覚タスク司令（設計提案）
├── mode-x-er-vla/   Mode X-ER-VLA: Gemini Robotics-ER + VLA 統合モード
└── mode-x/          旧 Mode X 互換参照（新規設計は mode-x-er / mode-x-er-vla）
```

> 「何を作るか」（設計）= `architecture/` `shared/` `mode-*/`。「どう開発するか」（プロセス・運用・教訓）= [`dev/`](dev/README.md)。強制力のある規約は [`.claude/rules/`](../.claude/rules/)、現況は [STATUS.md](STATUS.md)。

## shared/ — モード非依存

| ファイル | 内容 |
|---------|------|
| [00-project-overview](shared/00-project-overview.md) | プロジェクト概要・目的・成果物 |
| [01-budget-and-procurement](shared/01-budget-and-procurement.md) | 予算・調達リスト |
| [02-hardware-design](shared/02-hardware-design.md) | ハードウェア設計（Yahboom, Jetson, LiDAR等） |
| [04-diorama-layout](shared/04-diorama-layout.md) | 倉庫レイアウト設計（1.8m×0.9m） |
| [05-video-storyboard](shared/05-video-storyboard.md) | YouTube映像構成・ストーリーボード |
| [07-research-notes](shared/07-research-notes.md) | 調査メモ・未検証事項（T1-T12） |
| [09-navigation-internals](shared/09-navigation-internals.md) | AMCL・Nav2・SLAM内部設計 |
| [10-system-qanda](shared/10-system-qanda.md) | システム設計Q&A |

## architecture/ — 共通基盤

| ファイル | 内容 |
|---------|------|
| [03-software-architecture](architecture/03-software-architecture.md) | ソフトウェアアーキテクチャ全体 |
| [06-implementation-phases](architecture/06-implementation-phases.md) | 実装フェーズ計画（Phase 0-6） |
| [08-llm-bridge-common](architecture/08-llm-bridge-common.md) | LLM Bridge共通設計（LLM Client IF, Langfuse, コスト, フォールバック） |
| [12-infrastructure-common](architecture/12-infrastructure-common.md) | 共通基盤（Emergency Guardian, State Cache, Emergency後同期, 責務分離） |
| [13-hermes-setup](architecture/13-hermes-setup.md) | Hermes Gateway セットアップ・運用ガイド（config.yaml/.env テンプレ、起動手順、両モード対応） |
| [14-character-llm-negotiation](architecture/14-character-llm-negotiation.md) | キャラLLM + 交渉プロトコル設計（Mode A メイン回の中核） |
| [15-mcp-platform](architecture/15-mcp-platform.md) | MCPプラットフォーム（Hermes Agent, Warehouse MCP Server, Policy Gate, 競合状態の防止） |
| [16-repository-and-conventions](architecture/16-repository-and-conventions.md) | リポジトリ構成・パッケージ命名・msg型・gen_store・モデル方針・ブランチ戦略（実装の起点） |
| [17-development-workflow](architecture/17-development-workflow.md) | 開発の進め方と分担（worktree並列の実行手順書・契約凍結・依存グラフ・マージ順） |
| [18-gcp-serverless-cost-comparison](architecture/18-gcp-serverless-cost-comparison.md) | Slack Gateway のサーバーレス化検討（Always Free=$0 の現状 vs Cloud Run scale-to-zero・実測・PoC計画） |
| [19-environments-and-config](architecture/19-environments-and-config.md) | 環境分離 dev/stg/prod（軸A: config/secrets切替・WAREHOUSE_ENV・base+overlay・prodはタグ） |
| [20-dev-quality-and-testing](architecture/20-dev-quality-and-testing.md) | 開発品質・テスト戦略（Ruff/pytest/pre-commit/CI/Playwright・安全契約テスト・テストピラミッド） |
| [21-eval-sdk-extraction](architecture/21-eval-sdk-extraction.md) | Eval SDK 抽出（`eval_sdk`：Langfuse trace/score・KPI をドメイン非依存に抽出する設計提案） |
| [22-web-observability](architecture/22-web-observability.md) | Web Observability（Mode A 会話・稟議のリアルタイム観測基盤：`web_bridge` + Next.js `web/console`、Langfuse 整合） |

## productization/ — 商用再利用 Box 設計

| ファイル | 内容 |
|---------|------|
| [README](productization/README.md) | 商用再利用 Box 設計の位置づけ・基本方針 |
| [01-commercial-box-map](productization/01-commercial-box-map.md) | L4/L3/Contract/Governance/Traffic/Nav/Safety/Hardware/Eval の box map |
| [02-l4-robotics-bridge-box](productization/02-l4-robotics-bridge-box.md) | LLM Bridge / Robotics Bridge / ER / VLA / Langfuse の L4 box 設計 |
| [03-l3-planning-core-box](productization/03-l3-planning-core-box.md) | L3 Planning Core の商用再利用 box 設計 |
| [04-box-storage-and-reuse-guidelines](productization/04-box-storage-and-reuse-guidelines.md) | box の保管方法、成熟度、site profile、fixture、分離基準 |
| [05-decision-observability-and-tooling](productization/05-decision-observability-and-tooling.md) | L3 / Contract / Governance / Safety の decision log、reject 集計、既存 tool と自作範囲 |
| [06-oss-reuse-and-box-small-designs](productization/06-oss-reuse-and-box-small-designs.md) | L4 sub-box / Traffic / Navigation / Hardware / Eval の小設計と OSS 再利用方針 |
| [07-layer-tool-decision-matrix](productization/07-layer-tool-decision-matrix.md) | layer / box ごとの OSS / tool 採用・候補・不採用・要 spike・採用条件 |
| [08-navigation-hardware-eval-gates](productization/08-navigation-hardware-eval-gates.md) | Navigation / Hardware / Eval の acceptance gate と reason_code catalog |
| [09-run-manifest-and-plugin-composition](productization/09-run-manifest-and-plugin-composition.md) | box / plugin の run manifest、plugin manifest、pluggy / DuckDB 等の使いどころ、WO と eval_sdk の関係 |
| [10-llm-assisted-rule-authoring](productization/10-llm-assisted-rule-authoring.md) | 顧客 rule を LLM 支援で site profile / plugin profile / fixture / simulation へ落とす設計 |
| [l3-rule-authoring-detail](productization/l3-rule-authoring-detail.html) | LLM 支援 rule authoring の HTML 図解 |
| [productization/l4](productization/l4/README.md) | L4 内部 sub-box の layer skeleton。Model Transport / Adapter の詳細設計 |

## mode-a/ — LLM単独交通管理

| ファイル | 内容 |
|---------|------|
| [README](mode-a/README.md) | Mode A/B構成概要・起動手順 |
| [08a-llm-bridge-mode-a](mode-a/08a-llm-bridge-mode-a.md) | LLM Bridge Mode A/B固有（situation JSON, system prompt, 6アクション） |
| [11a-traffic-mode-a](mode-a/11a-traffic-mode-a.md) | 交通管理 Mode A/B（NoTrafficManager, SimpleTrafficManager） |
| [12a-integration-mode-a](mode-a/12a-integration-mode-a.md) | システム統合 Mode A/B（Nav2 Bridge, systemd構成） |

## mode-c/ — LLM + Open-RMF

| ファイル | 内容 |
|---------|------|
| [README](mode-c/README.md) | Mode C構成概要・起動手順 |
| [08c-llm-bridge-mode-c](mode-c/08c-llm-bridge-mode-c.md) | LLM Bridge Mode C固有（situation JSON, system prompt, 3アクション） |
| [11c-traffic-mode-c](mode-c/11c-traffic-mode-c.md) | 交通管理 Mode C（RMFTrafficManager, Open-RMF） |
| [12c-integration-mode-c](mode-c/12c-integration-mode-c.md) | システム統合 Mode C（Fleet Adapter, Open-RMF連携） |

## mode-x-er/ — Gemini Robotics-ER 視覚タスク司令（設計提案）

| ファイル | 内容 |
|---------|------|
| [README](mode-x-er/README.md) | Mode X-ER の位置づけ・正本ファイル・未凍結事項 |
| [01-architecture-and-flow](mode-x-er/01-architecture-and-flow.md) | L4→L3→L2→L1/L0 の data flow、X-lite / X-rmf |
| [02-l3-planning-core](mode-x-er/02-l3-planning-core.md) | Validator / Visual Resolver / Task Graph Executor / Command Compiler 詳細 |
| [03-er-adapter-skeleton](mode-x-er/03-er-adapter-skeleton.md) | Gemini Robotics-ER 単体 adapter skeleton と integration gates |
| [04-er-input-modalities-and-stt](mode-x-er/04-er-input-modalities-and-stt.md) | ER の入力モダリティ（audio 直受け）・STT の要否・ER 単体時の Fusion 必要性 |
| [05-operator-feedback-and-voice-response](mode-x-er/05-operator-feedback-and-voice-response.md) | 拒否/要確認/緊急を「どの箱の・どの理由か」音声で人へ返す **L4 Operator Feedback Box**（新 sub-box・出力）＋図解 |
| [06-unfrozen-contract-resolutions](mode-x-er/06-unfrozen-contract-resolutions.md) | 未凍結7ブロッカーの凍結/延期判定（FREEZE-NOW / DEFER / NEEDS-PROBE）＋ contract PR ロードマップ。**transport 判定（§5＋補遺）＝音声/画像/text の Hermes vs direct 実測結果**の正本 |
| [07-implementation-status](mode-x-er/07-implementation-status.md) | **ER→L3 実装ステータス（想定 vs 現状 on main）**＋図解 [implementation-status.html](mode-x-er/implementation-status.html)。offline チェーンは frozen Command まで完成・live-send(#389)/L2-L1-L0 作動(XER6) 未着地。全 file:line 裏取り |
| [dev/07 live runbook](dev/07-mode-x-er-live-e2e-runbook.md) | Mode X-ER live ER→L3→Langfuse 実走 operator runbook（gateway 起動・preflight・課金 gate・scoped 承認文言・honest limits。設計は本 mode-x-er/、live 手順は dev/ 側） |
| [deploy/hermes/er-audio-fork/](../deploy/hermes/er-audio-fork/) | **ER audio-via-Hermes `input_audio` fork パッケージ**（#357）＋ `hlf-g0-langfuse` Option-D（#360）の配備成果物。`apply-fork.sh` / `run-er-gateway.sh` / patch。**TARGET=default-Hermes**（fork 経由で audio も Hermes）／ **CURRENT（shipped）=audio は direct**（fork は未 productionize・direct は恒久 fallback。doc `mode-x-er/06` §5 補遺:263-271） |

## mode-x-er-vla/ — Gemini Robotics-ER + VLA 統合モード（設計提案）

| ファイル | 内容 |
|---------|------|
| [README](mode-x-er-vla/README.md) | Mode X-ER-VLA の位置づけ・Mode X-ER との差分・未凍結事項 |
| [01-integration-architecture](mode-x-er-vla/01-integration-architecture.md) | ER + VLA 統合 architecture と data flow |
| [02-openvla-research-plan](mode-x-er-vla/02-openvla-research-plan.md) | OpenVLA を ER と統合して使う価値・制約を調べる観点 |
| [03-simulation-and-safety-gates](mode-x-er-vla/03-simulation-and-safety-gates.md) | ER+VLA の Isaac Sim / offline fixture / 実機接続前 safety gates |
| [04-openvla-use-cases-and-control-flow](mode-x-er-vla/04-openvla-use-cases-and-control-flow.md) | OpenVLA の用途、L3 による起動タイミング、把持/配置 subtask の制御フロー |

## mode-x/ — 旧 Mode X 互換参照

| ファイル | 内容 |
|---------|------|
| [README](mode-x/README.md) | 旧 Mode X 設計。新規判断は `mode-x-er/` または `mode-x-er-vla/` を正本にする |
| [08x-robotics-bridge-mode-x](mode-x/08x-robotics-bridge-mode-x.md) | 旧 Robotics Bridge Mode X 詳細。新規判断は `mode-x-er/` または `mode-x-er-vla/` を正本にする |

## setup/ — デプロイ手順

| ファイル | 内容 |
|---------|------|
| [jetson-deploy](setup/jetson-deploy.md) | Jetson prod 常駐化（systemd / 監視・デプロイ手順） |

## jetson/ — 実機投入前ゲート

| ファイル | 内容 |
|---------|------|
| [01-fidelity-and-validation](jetson/01-fidelity-and-validation.md) | Jetson 忠実度ギャップ・dev/stg→prod de-risk（#127） |

## モード切替

> 下記は**要点の抜粋（例示）**。ロード可能な正本スキーマは `config/warehouse.base.yaml` + `config/<env>/warehouse.yaml`（doc13 §3.3）。

```yaml
# 例: モード別設定の要点（正本は config/warehouse.base.yaml）
traffic_mode: "open-rmf"   # Mode C: LLM + Open-RMF（主方針）/ "simple"=Mode B / "none"=Mode A（動画メイン回）

# サイクル長（総サイクル。config 実キー = cycle.mode_a_seconds / mode_c_seconds）
cycle:
  mode_a_seconds: 3        # Mode A: 約3秒/サイクル（待機 1s + 応答 ~2s）。dev は 120（~2分スパン, config/dev/warehouse.yaml）
  mode_c_seconds: 5        # Mode C: 約5秒/サイクル（待機 3s + 応答 ~2s）
# ※ scheduler.resolve_cycle_wait が「待機 = 総サイクル − 応答(~2s)」に変換し cycle_wait_sec として使う
#   （doc08:125-128）。欠落/不正/非正は code 既定 1.0/3.0s へ fail-open。env overlay / WAREHOUSE__* で上書き可（doc19）
```

> **キャラLLM パラメータ**（`enabled` / `model: opus` / `max_tokens: 60` / `negotiation_timeout_sec` / `max_turns_per_bot`）は doc14 の設計パラメータで、現状どの config にも未定義（Mode A メイン回の実装時に config 化）。

> **位置づけ補足**: 動画的には **Mode A がメイン回**（LLMがminicarを動かしてみたの主役）、Mode C は**実用検証回**（Open-RMFというチートを使うとこんなに上手く動く）。技術主方針としては Mode C を採用。

## adr/ — 決定記録（Architectural Decision Records）

hard-to-reverse な設計判断とその理由を `NNNN-slug.md` で記録する（設計 doc の `NN-xx` とは別クラス）。索引・フォーマット・判定は [adr/README](adr/README.md) と [ADR-FORMAT](../.claude/skills/domain-modeling/ADR-FORMAT.md)。決定を対話で詰めながら書き起こす入口は `/grill-with-docs`。

| ファイル | 内容 |
|---------|------|
| [adr/README](adr/README.md) | ADR 一覧・命名・いつ起こすか（3条件）・retrospectives との違い |
| [0003-bridge-local-manifest-composition](adr/0003-bridge-local-manifest-composition.md) | bridge-local run manifest + fail-closed plugin composition を A案で標準化（manifest resolution 層／namespaced plugin code〔9-enum 非改変〕／advisory trust／ISOLATE_PLUGIN／safety-critical profile hash gate）。実装 = offline spike 済・配線 XER6 pending |
| [0002-er-in-hermes-standard](adr/0002-er-in-hermes-standard.md) | ER-in-Hermes を標準 transport に採用（fork gateway 8644 一本で全 modality／`direct`=緊急 fallback／Langfuse Pattern A 現行・Pattern B は HLF gate 後）。実装は TARGET |
| [0001-adopt-grill-with-docs-and-canonical-glossary](adr/0001-adopt-grill-with-docs-and-canonical-glossary.md) | docs authoring 規律として grill-with-docs skill 群＋単一正準 `docs/GLOSSARY.md`＋ADR 実践を採用 |
