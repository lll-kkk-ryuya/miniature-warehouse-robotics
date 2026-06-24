---
name: html-explainer
description: >
  設計・分類・データフロー・アーキテクチャを「自己完結のダークモード HTML（図解）」で
  説明するときに使う。外部 CDN/依存なしの単一 .html を docs/ 配下に作り、色分けした
  box/node・レイヤマップ・横フロー・before/after 対比・docs 紐付け表で図示する。
  「html で図解して」「ダイアグラムにして」「box の切り分けを図で」「アーキ図を HTML で」
  と頼まれたとき、または分類/境界/フローを文章だけでなく図で見せたいときに起動する。
  配色は常にダークモード（color-scheme: dark 固定）。
allowed-tools: Read, Grep, Glob, Bash, Write
---

# html-explainer — 自己完結ダークモード HTML 図解の生成

設計・分類・フローを **単一の自己完結 HTML（外部依存ゼロ・ダークモード固定）** で図解する。
正本は常に docs/code の .md であり、本 HTML は **図解（補足資料）**。主張は必ず辿れる
`file:line` を併記する（[docs-first.md](../../rules/docs-first.md)）。

実証済みの完成例: `docs/productization/box-taxonomy.html`（box 切り分けの図解）。
雛形: [`template/dark-explainer.html`](template/dark-explainer.html)（このスキル同梱・コピー元）。

## 0. 不変条件（絶対に外さない）

1. **自己完結**: `<style>` をインラインに持ち、**外部 CDN / JS ライブラリ / 画像 / フォントに依存しない**
   （Mermaid 等の CDN も使わない）。オフライン・file:// でも開ける。
2. **ダークモード固定・落ち着いた配色**: 雛形の `:root` CSS 変数（`color-scheme: dark`）をそのまま使う。
   ライトテーマを別途作らない（ユーザー標準＝ダーク）。**色はすべて `:root` 変数で駆動**し、本文に
   16進をハードコードしない（配色変更は `:root` 一箇所で済む）。具体値は §カラーパレット。
3. **docs-first**: 図の各要素に **辿れる `file:line`**（repo-relative path + 行 or symbol）を併記。
   docs に無い契約・トピック・しきい値を **発明しない**。例示と凍結契約を区別する。
4. **配置**: 出力は対象 docs の隣に置く（例 `docs/productization/<topic>.html`）。
   一次ソースの .md を置き換えず、補足図解として添える。

## カラーパレット（固定・落ち着いた Cursor 風ニュートラル・ダーク）

ユーザー標準。雛形 `template/dark-explainer.html` の `:root` がこの値を持つ。**背景は黒に近い無彩色、
文字ははっきりした白、アクセントは calm（彩度低め・読みやすさ優先）**。新規 HTML もこの `:root` を流用する。

```css
:root{
  color-scheme: dark;
  --bg:#1a1a1a; --bg2:#141414; --card:#222222; --card2:#262626; --surface:#2d2d2d;
  --ink:#ededed;        /* 本文＝はっきりした白 */
  --muted:#c4c4c4;      /* 補足＝薄すぎない灰 */
  --faint:#8f8f8f; --line:#3a3a3a; --code:#141414;
  --badge-ink:#1a1a1a;  /* 明るい badge 上の文字＝背景色 */
  --head:#dcdcdc;       /* 見出し/表ヘッダ */
  --box:#7aa2f7;     --box-bg:rgba(122,162,247,.13);   /* blue   */
  --sub:#9ece6a;     --sub-bg:rgba(158,206,106,.12);   /* green  */
  --seam:#e0af68;    --seam-bg:rgba(224,175,104,.12);  /* amber  */
  --plugin:#bb9af7;  --plugin-bg:rgba(187,154,247,.12);/* purple */
  --demoted:#9a9a9a; --demoted-bg:rgba(154,154,154,.10);/* gray  */
  --red:#f7768e; --yellow:#e0c07a; --teal:#7dcfff;     /* dref=teal */
}
```

ルール: ① 文字色は `--ink`/`--muted`/`--head` のみ（薄い灰を本文に使わない）。② badge 等の濃色背景上の文字は
`--badge-ink`。③ アクセントは上記5色＋`--red/--yellow/--teal` に限定し統一感を保つ。④ 配色を変えるときは
`:root` だけ直す（本文ハードコード禁止＝grep `#[0-9a-f]{6}` が本文 0 件になるのが理想）。

## 1. 手順

1. **対象を確定し正本を実 Read**: 図示する設計の正本 .md と、参照する凍結契約/コードを
   `Read`/`Grep` で開く。図に載せる `file:line`・symbol を**自分で裏取り**（記憶で書かない）。
2. **雛形をコピー**: `template/dark-explainer.html` を出力先 `docs/.../<topic>.html` にコピー。
   `Read` で雛形を読み、`Write` で実内容に置換する。
3. **必要なコンポーネントだけ残す**（雛形の各 `<!-- ... -->` 区画から取捨）:
   - **badge / node**: 種別・カテゴリの色分けカード（`.n-box/.n-sub/.n-seam/.n-plugin/.n-demoted`）。
   - **layer map**: レイヤを縦に積み、各層に node を並べる（`.layer` + `.row`）。
   - **flow**: 横方向の採用経路/データフロー（`.flow` + `.arr` + `.seamtag` + `.lane`）。
   - **compare**: ❌旧/✅新 の before/after（`.compare .panel.bad/.good`）。
   - **table**: 各要素 → docs 正本 / repo 実体 の紐付け表（`impl-yes`/`impl-no` で実装有無）。
4. **PLACEHOLDER を実内容へ**: タイトル・凡例・各 node・表を、裏取りした `file:line` 付きで埋める。
5. **検証**: タグ均衡を機械チェック（下記スニペット）。`docs/**` を触ったので
   `python3 scripts/check_consistency.py` を走らせ 0 ERROR を確認（[consistency-check.md](../../rules/consistency-check.md)）。
6. **プレビュー（Cursor 内表示）**: `file://` は Simple Browser に弾かれることがあるため localhost 経由:
   ```bash
   python3 -m http.server 8123 --directory docs/<dir>   # バックグラウンド可
   ```
   Cursor: `Cmd+Shift+P` → `Simple Browser: Show` → `http://localhost:8123/<topic>.html`。
   外部ブラウザなら `open docs/<dir>/<topic>.html`。確認後はサーバを止める。

## 2. タグ均衡チェック（提出前に必ず）

```bash
python3 - <<'PY'
from html.parser import HTMLParser
class V(HTMLParser):
    def __init__(s):
        super().__init__(); s.st=[]; s.void={'meta','br','img','hr','input','link','area','base','col','embed','source','track','wbr'}; s.err=[]
    def handle_starttag(s,t,a):
        if t not in s.void: s.st.append(t)
    def handle_endtag(s,t):
        if t in s.void: return
        if s.st and s.st[-1]==t: s.st.pop()
        elif t in s.st:
            while s.st and s.st[-1]!=t: s.err.append('implicit '+s.st.pop())
            s.st and s.st.pop()
        else: s.err.append('stray </%s>'%t)
import sys; d=open(sys.argv[1] if len(sys.argv)>1 else 'docs/x.html',encoding='utf-8').read()
p=V(); p.feed(d)
print('unclosed:', p.st[-6:] or 'none', '| errors:', p.err[:8] or 'none',
      '| div', d.count('<div'),'/',d.count('</div>'))
PY
# 末尾に対象 .html のパスを付けて実行
```

## 3. やってはいけない

- 外部 CDN / JS ライブラリ / Mermaid / Web フォント / 画像への依存（自己完結を壊す）。
- ライトテーマ化（ユーザー標準はダーク固定）。
- `file:line` 無しの図示・記憶での引用（裏取り必須・[docs-first.md §引用](../../rules/docs-first.md)）。
- 一次ソース .md を HTML で置き換える（HTML は補足図解。設計の正本は .md）。
- `.claude/**` や対象外パスの編集（このスキルの編集境界は出力先の `docs/**` と本 HTML のみ）。

## References
- 同梱雛形: [`template/dark-explainer.html`](template/dark-explainer.html)
- 完成例: `docs/productization/box-taxonomy.html`
- 正本ルール: [docs-first.md](../../rules/docs-first.md) / [consistency-check.md](../../rules/consistency-check.md)
- Codex 版: `.agents/skills/html-explainer/SKILL.md`（同手順の簡約英語版・同じ雛形を参照）
