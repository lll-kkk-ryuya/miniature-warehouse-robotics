# web/e2e — Playwright E2E（WO画面 / rmf-web、Phase 4）

倉庫ダッシュボード（WO画面・rmf-web）の E2E テスト。**Web UI は Phase 4 で実装**するため、現状はスキップ状態の雛形。

## セットアップ
```bash
cd web/e2e
npm ci
npm run install-browsers   # playwright install --with-deps chromium
```

## 実行
```bash
BASE_URL=http://localhost:3000 npm test   # 対象ダッシュボードのURLを指定
npm run report                            # HTMLレポート表示
```

## CI
`.github/workflows/ci.yml` の `web-e2e` ジョブで実行（現状 `if: false`。Phase 4 で UI 実装後に有効化し、`web/**` 変更時に走らせる）。

## TODO（Phase 4）
- `tests/dashboard.spec.ts` の `test.skip` を外し、実フロー（ロボット位置のリアルタイム描画・KPI更新・LLM reasoning ログ）を検証する。
