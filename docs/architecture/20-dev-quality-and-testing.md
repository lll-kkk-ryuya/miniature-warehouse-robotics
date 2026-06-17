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
| CI | **GitHub Actions** | push/PR で lint/test・docs整合・firmware 安全 unit 等を自動実行（5 job・§4） | `.github/workflows/ci.yml` |
| Web E2E | **Playwright** | WO画面 / rmf-web の E2E（Phase 4） | `web/e2e/` |
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
- **LLM Bridge / MCP は偽入力で先行E2E**（doc16 §9・§11）: 偽トピック・偽 `state.json` でGazebo/実機なしに検証できる設計にする。Hermes/Langfuse 実接続は secrets・課金・ローカル gateway に依存するため常時 CI に入れず、`tests/live` の env-gated smoke（例: `WAREHOUSE_LIVE_HERMES=1`）として分離する。

## 3. Lint / Format 規約

- Ruff `target-version = py312`（ROS 2 Jazzy / Ubuntu 24.04）、`line-length = 100`。
- 選択ルール: `E,W`(PEP8) `F`(pyflakes) `I`(isort) `B`(bugbear) `UP`(pyupgrade) `N`(naming) `SIM`(simplify)。
- フォーマットは `ruff format`（black互換）。`.claude/rules/code-style.md`（YAML 2スペース、launchは.py）とも整合。
- C++（ROSノード）は別途 Google C++ Style（`.claude/rules/code-style.md`）。Ruffの対象外。

## 4. CI フロー（GitHub Actions）

- **トリガー**: 全ブランチへの push と、main への PR。
- **常時 job（全 push/PR）**: `python-quality`（`ruff check`→`ruff format --check`→`pytest`）／ `langfuse-api-contract`（`langfuse>=4.9,<5` を入れ、`eval_sdk.tracer` が依存する実 SDK call surface を鍵なし・ネットワークなしで検査）／ `consistency`（`scripts/check_consistency.py`・docs↔code 整合・0 ERROR 必須）／ `firmware-safety`（host R-26 速度クランプ unit ＋ キネマティクス unit ＋ skeleton compile・ESP32/PlatformIO 不要）。1つでも失敗で赤。
- **条件付き job**: `governance`（PR 時のみ・`warehouse_interfaces` 変更に contract ラベル必須＋他トラック内部 import 禁止）／ `web-e2e`（Playwright・Web UI 未実装で現状 `if: false`・Phase 4 で有効化）。通常の `python-quality` は Langfuse 非依存を維持し、optional SDK drift は `langfuse-api-contract` に隔離する。
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
| Phase 4 | WO画面/rmf-web 実装に合わせ Playwright E2E を有効化（`web-e2e` の `if:` 解除） |

## 8. Langfuse 観測 taxonomy（Phase 4 LLM 比較の弁別軸）

追記日: 2026-06-05（#88 / wo-metrics）

> **位置づけ**: Phase 4 の **4 provider × 3 交通モード**比較で、Langfuse の trace / observation / score に**どの弁別子をどこに載せるか**を横断集約する。**正本は [08](08-llm-bridge-common.md) §比較検証ログ（:339-386）・§比較計測の追加設計（:489-518）と [13 §7.5](13-hermes-setup.md)（:512-520）**。本節はそれらの **taxonomy リファレンス（地図）**であり、doc08/doc13 が正・矛盾時は doc08/doc13 を優先する。**凍結契約 `warehouse_interfaces` 変更なし**（全弁別子は Langfuse 側のラベルであり ROS メッセージ契約ではない。doc08:487 / doc13:519）。

### 8.1 弁別子 → 格納先 → owner 一覧

| 弁別子 | 値域 | 格納先（Langfuse） | owner | 出所（file:line） |
|---|---|---|---|---|
| `provider` | claude / openai / google / xai | trace tag[0]・`session_id`・score `metadata.provider` | #4 trace（env `WAREHOUSE_PROVIDER`）／ #6 score | doc08:367,375,381,487 |
| `mode` | A/B/C ↔ traffic none/simple/open-rmf（※§8.4） | trace tag[1]・`session_id`・score `metadata.mode` | #4 trace ／ #6 score | doc08:375,381,487,512 |
| `gen_id` | int（1世代＝1サイクル） | trace `metadata.gen_id`・**trace seed 後半**・score `metadata.gen_id` | #4 trace ／ #6 score | doc08:183,375 / doc13:516,519 |
| `turn` | int（3秒サイクル1回） | **trace `name`** | #4 | doc08:329,340 |
| `run_id` / session | `run_{mode}_{provider}_{scenario}_{ts}` | trace `session_id`・**trace seed 前半**・score `metadata.run_id` | #4 `build_session_id` ／ 共有 env `WAREHOUSE_RUN_ID` | doc08:381 / doc13:516,519 / `warehouse_llm_bridge/tracing.py:28-36` |
| `scenario` | 5 比較シナリオ | `session_id` 内に符号化 | #4（env `WAREHOUSE_SCENARIO`） | doc08:381 / doc06:249-253 |
| `robot` | bot1 / bot2 | score `metadata.robot`（efficiency leg が per-leg 付与）／ fallback で score 名 `efficiency_bot1` | #6 | doc08:360,367,369 |

- **trace seed**: `trace_id = create_trace_id(seed=f"{run_id}:{gen_id}")`（32hex no-dash・#4/#6 が決定的に同一導出、doc13:516,519）。`turn`（trace name）と `gen_id`（seed 後半）はいずれも「1サイクル」を指す**同一粒度**（doc08:329 が `turn`＝3秒サイクル1回、:183 が `gen_id`＝1サイクル=1世代）。score 側は `gen_id` を per-cycle キーとして metadata に持つ（`turn` は trace name で #4 所有）。

### 8.2 trace tags と score metadata（v4 の制約と owner 分離）

Langfuse v4 では **score は tag を持てない**（doc08:367）。よって弁別子の格納先が trace と score で分かれる:

- **trace**（#4 Bridge 所有, `warehouse_llm_bridge/tracing.py:171` が `tags=[provider, mode]` を emit・:96 は記述）: `langfuse_tags=[provider, mode]`（doc08:375）＋ `gen_id` metadata ＋ `session_id`。tag で filter / group 可。
- **score**（#6 wo 所有, `warehouse_orchestrator/score_send.py` の `build_score_metadata`）: tag 無し → 全ラベルが metadata `{run_id, mode?, provider?, gen_id?}`（doc08:487 / :369）＋ efficiency leg の `robot`（doc08:369）。

**wo の taxonomy 定数**（`warehouse_orchestrator/tags.py`、本 #88 で追加）が両者の語彙を**単一ソース**化し、trace 側・score 側・テストの綴りがドリフトしないようにする:

- `TAG_KEYS = (run_id, mode, provider, gen_id)` ＝ score metadata キー集合（doc08:487）。`build_score_metadata` がこれを参照する＝off-taxonomy なキーを発明しない。`TAG_KEY_ROBOT` は per-leg キー（doc08:369）で `TAG_KEYS` には**含めない**。
- `provider_tags(provider, mode) → [provider, mode]` ＝ trace tag リスト形（doc08:375）の taxonomy 単一ソース。**live emit は #4（`tracing.py`）**であり、cross-lane import 禁止（parallel-workflow §2.1）のため #4 はこの関数を import しない。wo 側では reference + test 用の **inert**（`langfuse_sink` の予約 `SCORE_*` 名と同型）。wo 自身は score（tag 無し）のみ送るため、消費するのは `TAG_KEYS` であり `provider_tags` ではない。

### 8.3 Phase 4 比較クエリ（設計参照・Phase 4 seam）

- **12構成 = 4 provider × 3 交通モード（none / simple / open-rmf）**（doc08:512）。
- **2軸の別**（doc06:275-276）: `provider` ＝ **LLM 比較軸**（応答速度・正確性・cost 等。doc08 §比較指標）、交通 `mode` ＝ **交通モード軸**（`deadlock` / `collision_free` / `replans` 等。doc08 §比較計測の追加設計 :489-496）。taxonomy は両軸を支える。
- **集計（Phase 4 seam・PLAN・凍結契約ではない、doc08:508-518）**: 推奨は **Datasets + Experiments**（5 シナリオを dataset 化し `run_name="claude__open-rmf"` 等で provider×mode = 12 run、doc08:514）。**Metrics API** は score の tag 不可・`sessionId`/`traceId` を filter 可だが **group-by 不可**（doc08:515）のため、12構成の軸は **score `name` に符号化**（例 `result__claude__open-rmf`）するか、構成ごとに filter したクエリを `name` で group-by して 12 回反復する。**score metadata / sessionId への group-by 依存は避ける**（doc08:515）。詳細は doc08 §集計設計（:508-518）を正本参照。

### 8.4 未決・実機(Phase 3)残課題（移管せず列挙＝#88）

docs-first（残るおかしな点・暫定値を隠さず列挙）に従い、本スライス（実機不要・fake unit）で**確定できない**項目を残課題として明示する。**移管・実装はしない**:

1. **score metadata `mode` の値**が Mode-letter（A/B/C, doc08:360 例）か traffic_mode 文字列（none/simple/open-rmf, doc08:381,512）か**未確定**。Mode A は交通 none/simple の **2 つに跨る**ため、12構成グリッドへの配置には traffic_mode 文字列が要る。→ **#4（`session_id`/traffic_mode 所有）と調整のうえ Phase 3/4 で確定**。本スライスは score `mode` を**不透明 pass-through**のまま（コードで発明しない）。
2. **v4 score の `metadata` group-by 可否**が未確認（doc08:518）。不可なら §8.3 の score `name` 符号化を採用。実 Langfuse 4.9.x で確認＝Phase 3/4。
3. **実 Langfuse（4.9.x）でしか検証不可**（doc13:520 ①〜⑤）: Bridge-owned trace の 1 本集約（**二重 generation 無し**）／ `trace_id` 突合（#4↔#6 が同一 trace・audit `gen_id`+timestamp とも一致）／ Grok cost 計上（カスタムモデル定義）／ managed-prompt 連携。SDK 4.9 call surface スモークは `langfuse-api-contract` で CI 化済みだが、実 trace 送信・cost・prompt は **#88 Phase 3 残**（live credentials / Hermes 必須）。Hermes Gateway の `/health`・認証境界・任意 chat smoke はその前段の live smoke であり、この Langfuse 実トレース検証を置換しない。

---

## References

- `.claude/rules/code-style.md` / `.claude/rules/safety.md`
- [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md) §9・§11
- [08 - LLM Bridge 共通](08-llm-bridge-common.md) §比較検証ログ・§比較計測の追加設計（§8 taxonomy の正本）
- [13 - Hermes セットアップ](13-hermes-setup.md) §7.5（trace_id / 突合キー契約）
- [17 - 開発の進め方](17-development-workflow.md)
- [07 - 調査メモ](../shared/07-research-notes.md) R-26（テスト戦略の欠如）
- [Ruff](https://docs.astral.sh/ruff/) / [pytest](https://docs.pytest.org/) / [pre-commit](https://pre-commit.com/) / [Playwright](https://playwright.dev/)
