# web/e2e — Playwright E2E（web/console 観測コンソール）

`web/console`（Web Observability SPA・doc22 §15）の E2E スモーク。設計正本 [docs/architecture/22-web-observability.md](../../docs/architecture/22-web-observability.md) §13 S3 / §16。`web/console` とは **独立 sibling npm project**（workspace 化しない・doc22:340）で、BASE_URL（HTTP）越しにのみ結合する。

## 何を検証するか
gateway 無しで **static export がブートし、ダッシュボードの全パネルが mount する**こと（`/live` の 6 パネル＋ `/runs` picker・idle/empty 状態）。ライブのデータ経路（WebSocket）は live run で検証する。

## セットアップ
```bash
cd web/e2e
npm ci
npm run install-browsers   # playwright install --with-deps chromium
```

## 実行
```bash
# 1) 先に web/console を build（out/ を生成）
( cd ../console && npm ci && npm run build )
# 2) E2E 実行（playwright の webServer が ../console/out を :3000 で serve）
npm test
npm run report            # HTMLレポート表示
# 既に何かを配信している場合は BASE_URL を指定（webServer は skip される）
BASE_URL=http://localhost:8646 npm test
```

## CI
`.github/workflows/ci.yml` の **`web-e2e` ジョブ（常時実行・全 push/PR）**で走る。同 job が `web/console` を build → `out/` を serve → Playwright を実行する。build/型/lint の gate は別 job **`web-quality`**（eslint + `tsc --noEmit` + `next build`）。`paths` filter は付けない（paths-filter した required check は対象外 PR で skip→merge を block するため・doc22:348）。
