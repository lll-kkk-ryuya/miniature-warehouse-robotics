# 環境（dev / stg / prod）運用ルール

> 方式は軸A「デプロイ環境の分離」。設計の正本は [docs/architecture/19](../../docs/architecture/19-environments-and-config.md)。本書は守るべき運用規約のみ。

## 必須

- **環境は `WAREHOUSE_ENV`（dev | stg | prod）で選択**。未設定時は **dev** にフォールバック。
- 接続先 URL・パス・モード・sim/実機 はすべて **config から読む**。**環境名・エンドポイント・キーをコードにハードコードしない**。
- 設定は **base + overlay**: `config/warehouse.base.yaml` → `config/$WAREHOUSE_ENV/warehouse.yaml` → 環境変数（後勝ち）。共通値は base に一元化し、環境差分のみ `<env>/` に書く。

## Secrets（最重要・safety.md と一体）

- 実体は **`config/<env>/.env`**（`**/.env` で gitignore）。**コミット厳禁**（APIキー/トークン/WiFiパスワード）。
- リポジトリに置くのは **`.env.example`（プレースホルダのみ）**。
- **dev / stg / prod で別キーを使う**（本番事故・課金混在の防止）。

## prod の扱い（実機・本番）

- prod 接続・実機動作は **Emergency Guardian / 速度上限 0.3 m/s のテスト通過後のみ**（safety.md / doc16 §11）。
- **prod デプロイは git タグ**（`v0.x`）で固定。Jetson（別マシン）はそのタグを clone/checkout して実行（doc17 §4.0）。
- prod の config / secrets を変更する PR は安全レビューを必須とする。

## やってはいけない

- `config/<env>/.env`（実体）の commit。
- 環境別エンドポイント/モードのコード内ハードコード。
- 環境差分を base に書く（base は共通のみ）。
- dev のキーで prod へ接続、またはその逆。
