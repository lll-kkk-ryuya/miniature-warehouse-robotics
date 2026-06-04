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
| CI | **GitHub Actions** | push/PR で Ruff + pytest を自動実行 | `.github/workflows/ci.yml` |
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
- **LLM Bridge / MCP は偽入力で先行E2E**（doc16 §9・§11）: 偽トピック・偽 `state.json` でGazebo/実機なしに検証できる設計にする。

## 3. Lint / Format 規約

- Ruff `target-version = py312`（ROS 2 Jazzy / Ubuntu 24.04）、`line-length = 100`。
- 選択ルール: `E,W`(PEP8) `F`(pyflakes) `I`(isort) `B`(bugbear) `UP`(pyupgrade) `N`(naming) `SIM`(simplify)。
- フォーマットは `ruff format`（black互換）。`.claude/rules/code-style.md`（YAML 2スペース、launchは.py）とも整合。
- C++（ROSノード）は別途 Google C++ Style（`.claude/rules/code-style.md`）。Ruffの対象外。

## 4. CI フロー（GitHub Actions）

- **トリガー**: 全ブランチへの push と、main への PR。
- **ジョブ `python-quality`**: `ruff check` → `ruff format --check` → `pytest`。1つでも失敗で赤。
- **ジョブ `web-e2e`**: Playwright。Web UI 未実装のため現状 `if: false`（Phase 4 で有効化）。
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
| Phase 0.5 | 安全系ユニット（R-26）＋ LLM Bridge/MCP の偽入力テストを追加。Gazebo統合テストをCIに追加検討 |
| Phase 3 | ROS パッケージの colcon test / ament_lint を CI に追加 |
| Phase 4 | WO画面/rmf-web 実装に合わせ Playwright E2E を有効化（`web-e2e` の `if:` 解除） |

## References

- `.claude/rules/code-style.md` / `.claude/rules/safety.md`
- [16 - リポジトリ構成と実装規約](16-repository-and-conventions.md) §9・§11
- [17 - 開発の進め方](17-development-workflow.md)
- [07 - 調査メモ](../shared/07-research-notes.md) R-26（テスト戦略の欠如）
- [Ruff](https://docs.astral.sh/ruff/) / [pytest](https://docs.pytest.org/) / [pre-commit](https://pre-commit.com/) / [Playwright](https://playwright.dev/)
