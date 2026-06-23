# 開発品質・テスト戦略（安全に開発するための基盤）

作成日: 2026-05-29

> **位置づけ**: 実コードを書く前に「**壊れたものが main に入らない**」ための自動化基盤を定義する。
> 構造・分担は [16](16-repository-and-conventions.md) / [17](17-development-workflow.md)、リスクは [07](../shared/07-research-notes.md) を正本とし、本書は **lint / 型 / テスト / E2E / CI** の運用を定める。

---

## 1. ツールチェーン一覧

| 層 | ツール | 役割 | 設定 |
|----|--------|------|------|
| Lint + Format | **Ruff** | PEP8強制・import整列・bugbear・naming・自動整形（flake8/black/isort を一本化） | `pyproject.toml [tool.ruff]` |
| 型チェック | Mypy（将来） | 型ヒント検証（CLAUDE.md「型ヒント必須」）。本ブランチでは未導入、後続で追加 | — |
| ユニットテスト | **pytest** | ROS/実機なしで回る純ロジック検証 | `pyproject.toml [tool.pytest.ini_options]` |
| Pre-commit | **pre-commit** | commit前に Ruff + 衛生フック（秘密鍵検出等）を自動実行 | `.pre-commit-config.yaml` |
| CI | **GitHub Actions** | push/PR で lint/test・docs整合・firmware/web 安全 unit 等を自動実行（7 job・§4） | `.github/workflows/ci.yml` |
| Web E2E | **Playwright** | `web/console`（観測コンソール）の E2E スモーク（`out/` を serve して /live・/runs の panel mount を検証・doc22 §15）。WO画面/rmf-web は将来別途 | `web/e2e/` |
| ROS品質 | ament_lint / colcon test | ROSパッケージのlint/テスト。**現状は雛形**、Step 0 でパッケージ実体化後に本運用 | `ws/src/*/`（後続） |

> ⚠️ ROS 2 固有ツール（ament_lint_auto / colcon test）は、`ws/src/` の各パッケージが `package.xml`/`setup.py` を持って実体化（doc17 Step 0）してから本格運用する。

## 2. テスト戦略（テストピラミッド）

```
        ▲ 少
        │   E2E (Playwright)         … WO/rmf-web 画面（Phase 4、重い・遅い）
        │   統合テスト                … Gazebo E2E / 偽トピック（Phase 0.5〜）
        │   ユニット（安全契約）       … 速度クランプ/Policy Gate/バッテリー（毎push、軽い・速い）
        ▼ 多
```

- **土台はユニットテスト**: 実機・Gazebo・LLM API なしで CI が毎回回せるものを厚くする（doc16 §11）。
- **安全クリティカルは必須**（リスク R-26）: Emergency Guardian（速度クランプ ≤0.3 m/s）、Policy Gate（known_locations / バッテリー / stale / 重複）。`tests/contracts.py` は `warehouse_interfaces.safety` / `.locations` を再 export する**薄い shim**（単一ソース。歴史的 import パス `tests/unit/test_safety_contracts.py` 用に保持）。
- **LLM Bridge / MCP は偽入力で先行E2E**（doc16 §9・§11）: 偽トピック・偽 `state.json` でGazebo/実機なしに検証できる設計にする。Hermes/Langfuse 実接続は secrets・課金・ローカル gateway に依存するため常時 CI に入れず、`tests/live` の env-gated smoke（例: `WAREHOUSE_LIVE_HERMES=1`、`WAREHOUSE_LIVE_LANGFUSE_TAGS=1`）として分離する。

## 3. Lint / Format 規約

- Ruff `target-version = py312`（ROS 2 Jazzy / Ubuntu 24.04）、`line-length = 100`。
- 選択ルール: `E,W`(PEP8) `F`(pyflakes) `I`(isort) `B`(bugbear) `UP`(pyupgrade) `N`(naming) `SIM`(simplify)。
- フォーマットは `ruff format`（black互換）。`.claude/rules/code-style.md`（YAML 2スペース、launchは.py）とも整合。
- C++（ROSノード）は別途 Google C++ Style（`.claude/rules/code-style.md`）。Ruffの対象外。

## 4. CI フロー（GitHub Actions）

- **トリガー**: 全ブランチへの push と、main への PR。
- **常時 job（全 push/PR）**: `python-quality`（`ruff check`→`ruff format --check`→`pytest`）／ `langfuse-api-contract`（`langfuse>=4.9,<5` を入れ、`eval_sdk.tracer` が依存する実 SDK call surface を鍵なし・ネットワークなしで検査）／ `consistency`（`scripts/check_consistency.py`・docs↔code 整合・0 ERROR 必須）／ `firmware-safety`（host R-26 速度クランプ unit ＋ キネマティクス unit ＋ skeleton compile・ESP32/PlatformIO 不要）／ `web-quality`（setup-node 20 + eslint + `tsc --noEmit` + `next build`〔static export → `out/`〕で `web/console` を gate・doc22 §15/§16（:348））／ `web-e2e`（Playwright スモーク・`web/console/out` を serve して `/live`・`/runs` の panel mount を検証・doc22 §13 S3）。1つでも失敗で赤。
- **条件付き job**: `governance`（PR 時のみ・`warehouse_interfaces` 変更に contract ラベル必須＋他トラック内部 import 禁止）。`web-e2e` は `web/console` land に伴い常時 job へ移行（旧 `if: false` を解除）。通常の `python-quality` は Langfuse 非依存を維持し、optional SDK drift は `langfuse-api-contract` に隔離する。
- **ブランチ保護との関係**（doc16 §9 / doc17）: **main 直 push 禁止・ブランチ先行・PR必須**の運用と組み合わせ、`python-quality` を必須チェックにすることで「緑のPRだけが main に入る」状態を作る。

## 5. Pre-commit 運用

```bash
pip install pre-commit
pre-commit install          # 各clone/worktreeで1回
pre-commit run --all-files  # 手動で全ファイル
```
commit時に Ruff（lint+format）、末尾空白/改行修正、YAML/JSON検証、**秘密鍵検出**（`detect-private-key`、safety.md準拠）が走る。

## 6. 導入手順（このブランチ取得後）

```bash
pip install -e ".[dev]"     # ruff / pytest / pre-commit
pre-commit install
pytest                      # 安全契約テストが緑になることを確認
ruff check . && ruff format --check .
```

## 7. Phase 対応

| Phase | 本基盤の使い方 |
|-------|--------------|
| Step 0（doc17） | 本ブランチを main へマージし、全 worktree がこの CI/pre-commit を継承 |
| Phase 0.5 | 安全系ユニット（R-26）＋ LLM Bridge/MCP の偽入力テストを追加。Hermes Gateway 起動確認は env-gated live smoke に分離し、Gazebo統合テストをCIに追加検討 |
| Phase 3 | ROS パッケージの colcon test / ament_lint を CI に追加 |
| Phase 4 | WO画面/rmf-web の Playwright E2E を追加（`web-e2e` は `web/console` 観測コンソール向けに先行有効化済み・doc22 §13 S3） |

## 8. Langfuse 観測 taxonomy（Phase 4 LLM 比較の弁別軸）

追記日: 2026-06-05（#88 / wo-metrics）

> **位置づけ**: Phase 4 の **4 provider × 3 交通モード**比較で、Langfuse の trace / observation / score に**どの弁別子をどこに載せるか**を横断集約する。**正本は [08](08-llm-bridge-common.md) §比較検証ログ（:339-388）・§比較計測の追加設計（:491-520）と [13 §7.5](13-hermes-setup.md)（:512-520）**。本節はそれらの **taxonomy リファレンス（地図）**であり、doc08/doc13 が正・矛盾時は doc08/doc13 を優先する。**凍結契約 `warehouse_interfaces` 変更なし**（全弁別子は Langfuse 側のラベルであり ROS メッセージ契約ではない。doc08:489 / doc13:519）。

### 8.1 弁別子 → 格納先 → owner 一覧

| 弁別子 | 値域 | 格納先（Langfuse） | owner | 出所（file:line） |
|---|---|---|---|---|
| `provider` | claude / openai / google / xai | trace tag[0]・`session_id`・score `metadata.provider` | #4 trace（env `WAREHOUSE_PROVIDER`）／ #6 score | doc08:367,377,383,489 |
| `mode` | A/B/C ↔ traffic none/simple/open-rmf（※§8.4） | trace tag[1]・`session_id`・score `metadata.mode` | #4 trace ／ #6 score | doc08:377,383,489,514 |
| `gen_id` | int（1世代＝1サイクル） | trace `metadata.gen_id`・**trace seed 後半**・score `metadata.gen_id` | #4 trace ／ #6 score | doc08:183,377 / doc13:516,519 |
| `turn` | int（3秒サイクル1回） | **trace `name`** | #4 | doc08:329,340 |
| `run_id` / session | `run_{mode}_{provider}_{scenario}_{ts}` | trace `session_id`・**trace seed 前半**・score `metadata.run_id` | #4 `build_session_id` ／ 共有 env `WAREHOUSE_RUN_ID` | doc08:383 / doc13:516,519 / `warehouse_llm_bridge/tracing.py:28-36` |
| `scenario` | 5 比較シナリオ | `session_id` 内に符号化 | #4（env `WAREHOUSE_SCENARIO`） | doc08:383 / doc06:249-253 |
| `robot` | bot1 / bot2 | score `metadata.robot`（efficiency leg が per-leg 付与）／ fallback で score 名 `efficiency_bot1` | #6 | doc08:360,367,369 |
| `environment` | dev / stg / prod | **trace tag 末尾（`env=<v>`）**（score 側は未対応＝trace-only） | #4 trace（`paths.warehouse_env()`／env `WAREHOUSE_ENV`） | doc13:526,624（`env=` 慣用）／ doc19 ／ `warehouse_interfaces/paths.py:14-19` |

- **trace seed**: `trace_id = create_trace_id(seed=f"{run_id}:{gen_id}")`（32hex no-dash・#4/#6 が決定的に同一導出、doc13:516,519）。`turn`（trace name）と `gen_id`（seed 後半）はいずれも「1サイクル」を指す**同一粒度**（doc08:329 が `turn`＝3秒サイクル1回、:183 が `gen_id`＝1サイクル=1世代）。score 側は `gen_id` を per-cycle キーとして metadata に持つ（`turn` は trace name で #4 所有）。

### 8.2 trace tags と score metadata（v4 の制約と owner 分離）

Langfuse v4 では **score は tag を持てない**（doc08:367）。よって弁別子の格納先が trace と score で分かれる:

- **trace**（#4 Bridge 所有, live emit は `eval_sdk/tracer.py` の `propagate_attributes(tags=...)`〔doc21 で抽出。Bridge が構築・所有し `tracing.py` で re-export〕）: `langfuse_tags=[provider, mode, "prompt:<name>", env=<v>]`（doc08:377）。`prompt:<name>` は Prompt Management の prompt 弁別子、`env=<v>` は deployment 環境タグ `env=dev`/`env=stg`/`env=prod`（doc13:526,624 の `env=` 慣用）で、どちらも trace-only。**注**: Langfuse は保存時にタグを**アルファベット順へ正規化**するため fetch/filter は順序でなく**値**で行う＝tag[0]/tag[1] は emit 側のみ。trace はこれに加えて `gen_id` metadata ＋ `session_id` ＋ prompt metadata を持つ。tag で filter / group 可。
- **score**（#6 wo 所有, `warehouse_orchestrator/score_send.py` の `build_score_metadata`）: tag 無し → 全ラベルが metadata `{run_id, mode?, provider?, gen_id?}`（doc08:489 / :369）＋ efficiency leg の `robot`（doc08:369）。

**wo の taxonomy 定数**（`warehouse_orchestrator/tags.py`、本 #88 で追加）が両者の語彙を**単一ソース**化し、trace 側・score 側・テストの綴りがドリフトしないようにする:

- `TAG_KEYS = (run_id, mode, provider, gen_id)` ＝ score metadata キー集合（doc08:489）。`build_score_metadata` がこれを参照する＝off-taxonomy なキーを発明しない。`TAG_KEY_ROBOT` は per-leg キー（doc08:369）で `TAG_KEYS` には**含めない**。
- `provider_tags(provider, mode) → [provider, mode]` ＝ trace tag リスト形（doc08:377）の taxonomy 単一ソース。Bridge の live emit は `eval_sdk/tracer.py` の `extra_tags` 経由で prompt/env を追加するが、cross-lane import 禁止（parallel-workflow §2.1）のため #4 は wo の taxonomy 関数を import しない。wo 側では reference + test 用の **inert**（`langfuse_sink` の予約 `SCORE_*` 名と同型）。wo 自身は score（tag 無し）のみ送るため、消費するのは `TAG_KEYS` であり `provider_tags` ではない。`provider_tags` への prompt/env 同期は wo follow-up（#6）として残す（それまで live emit が正・taxonomy reference は追加 tag につき既知 stale）。

### 8.3 Phase 4 比較クエリ（設計参照・Phase 4 seam）

- **12構成 = 4 provider × 3 交通モード（none / simple / open-rmf）**（doc08:514）。
- **2軸の別**（doc06:275-276）: `provider` ＝ **LLM 比較軸**（応答速度・正確性・cost 等。doc08 §比較指標）、交通 `mode` ＝ **交通モード軸**（`deadlock` / `collision_free` / `replans` 等。doc08 §比較計測の追加設計 :491-498）。taxonomy は両軸を支える。
- **集計（Phase 4 seam・PLAN・凍結契約ではない、doc08:512-520）**: 推奨は **Datasets + Experiments**（5 シナリオを dataset 化し `run_name="claude__open-rmf"` 等で provider×mode = 12 run、doc08:516）。**Metrics API** は score の tag 不可・`sessionId`/`traceId` を filter 可だが **group-by 不可**（doc08:517）のため、12構成の軸は **score `name` に符号化**（例 `result__claude__open-rmf`）するか、構成ごとに filter したクエリを `name` で group-by して 12 回反復する。**score metadata / sessionId への group-by 依存は避ける**（doc08:517）。詳細は doc08 §集計設計（:512-520）を正本参照。
- **deployment 環境タグ `env=<v>`（dev/stg/prod, #88 追加）は 12構成の軸ではなく直交フィルタ**：env ごとに**別鍵**（doc19 §Secrets＝env ごとに別 `.env`／別鍵を規定。別「**プロジェクト**」運用は鍵の向き先次第で doc19 は明記しない）で分離されるため比較本線（Datasets+Experiments）に影響せず、UI での env 別フィルタ／自己記述／誤設定検知用のラベルとして使う。

### 8.4 未決・実機(Phase 3)残課題（移管せず列挙＝#88）

docs-first（残るおかしな点・暫定値を隠さず列挙）に従い、本スライス（実機不要・fake unit）で**確定できない**項目を残課題として明示する。**移管・実装はしない**:

1. **score metadata `mode` の値**が Mode-letter（A/B/C, doc08:360 例）か traffic_mode 文字列（none/simple/open-rmf, doc08:383,514）か**未確定**。Mode A は交通 none/simple の **2 つに跨る**ため、12構成グリッドへの配置には traffic_mode 文字列が要る。→ **#4（`session_id`/traffic_mode 所有）と調整のうえ Phase 3/4 で確定**。本スライスは score `mode` を**不透明 pass-through**のまま（コードで発明しない）。
2. **v4 score の `metadata` group-by 可否**が未確認（doc08:520）。不可なら §8.3 の score `name` 符号化を採用。実 Langfuse 4.9.x で確認＝Phase 3/4。
3. **実 Langfuse（4.9.x）でしか検証不可**（doc13:520 ①〜⑤）: Bridge-owned trace の 1 本集約（**二重 generation 無し**）／ `trace_id` 突合（#4↔#6 が同一 trace・audit `gen_id`+timestamp とも一致）／ Grok cost 計上（カスタムモデル定義）／ managed-prompt 連携（**offline 実装済**: [doc08 §Langfuse Prompt Management 方針](08-llm-bridge-common.md) / `prompts.py`。live seed・紐付けは本項）。SDK 4.9 call surface スモークは `langfuse-api-contract` で CI 化済みだが、実 trace 送信・cost・prompt は **#88 Phase 3 残**（live credentials / Hermes 必須）。Hermes Gateway の `/health`・認証境界・任意 chat smoke はその前段の live smoke であり、この Langfuse 実トレース検証を置換しない。`tests/live/test_langfuse_trace_tags_live.py` は Langfuse credentials がある環境で tracer 単体の persisted tag（`prompt:<name>` / `env=<v>`）を読み戻す手動 smoke として使う。
4. **`env=<v>` トレースタグ（#88・trace-only）の残差**: ① live emit 配線は `eval_sdk.tracer` の `extra_tags` 経由で完了したが、実 Langfuse 4.9.x での filterable 検証は #88 human gate。② 別プロジェクト構成では `env=<v>` はプロジェクト内で**定数**＝同一プロジェクト内フィルタの弁別力は低く、価値は自己記述／誤設定検知（dev 鍵→prod project 等）／前方互換（隔離は doc19 の env 別鍵が担保）。③ **`warehouse_orchestrator/tags.py` の `provider_tags` への `env` 同期は wo follow-up（#6）**（それまでは live emit が正・taxonomy reference は env につき既知 stale）。④ **score 側 `environment` metadata は未対応**（trace-only。必要時に doc08:489／§8.1 を先に拡張してから `build_score_metadata` に optional 追加）。⑤ **`env=` 名前空間は複数の直交軸を共有**: 本タグ（deployment `env=dev/stg/prod`）に加え doc13:526 の backend（`env=prod_vertex`）・doc13:624 Phase 5 の digital-twin source（`env=isaac_sim`/`env=real`）が同一 `env=` 前置を使う。タグは単純文字列ゆえ技術衝突はない（1 trace が `env=prod`＋`env=real` を併持しうる＝直交ラベル）が、フィルタ時の意味分裂に注意＝Phase 5 owner と値域整理を要調整（env-tag スコープ外）。
5. **trace の `prompt:<name>` タグ（本PR・trace-only）**: managed-prompt 連携の trace 可視化として `eval_sdk/tracer.py` の `extra_tags`/`extra_metadata`（domain 非依存・additive）経由で `tags=[provider, mode, "prompt:<name>", env=<v>]`＋metadata `{prompt_name, prompt_version, prompt_source, mode_label}` を付与（doc08 §Langfuse Prompt Management 方針）。**offline 実装済＋tracer 単体 live 実証済**（top-level tag で着地）。`env=<v>`（item 4）と同じ extra_tags 機構に相乗りし、両立する（Langfuse は値でフィルタ＝順序非依存）。

---

## References

- `.claude/rules/code-style.md` / `.claude/rules/safety.md`
- [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md) §9・§11
- [08 - LLM Bridge 共通](08-llm-bridge-common.md) §比較検証ログ・§比較計測の追加設計（§8 taxonomy の正本）
- [13 - Hermes セットアップ](13-hermes-setup.md) §7.5（trace_id / 突合キー契約）
- [17 - 開発の進め方](17-development-workflow.md)
- [07 - 調査メモ](../shared/07-research-notes.md) R-26（テスト戦略の欠如）
- [Ruff](https://docs.astral.sh/ruff/) / [pytest](https://docs.pytest.org/) / [pre-commit](https://pre-commit.com/) / [Playwright](https://playwright.dev/)
