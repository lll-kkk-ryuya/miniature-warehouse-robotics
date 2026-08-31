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

実証済みの完成例:
- `docs/productization/box-taxonomy.html` — box 切り分け（レイヤ×種別マップ）
- `docs/productization/layer-l4-detail.html` — L4 Super-Box 内部（入れ子 sub-box/seam）＋ Mode A/B/C・Mode X-ER・Mode X-ER-VLA 詳細図。**リッチ・コンポーネント語彙の参照元**（§4）
- `docs/productization/layer-l3-detail.html` — L3 Planning Core（core+plugin・stage）
- `docs/mode-x-er/mode-x-er-explainer.html` — **per-mode explainer**（1 モード 1 自己完結 HTML）の雛形例
- `docs/architecture/robot-architecture-tree.html` — **ディレクトリツリー型（architecture tree）**の雛形例（§5）。
  companion フロー図 `perception-localization-flow.html` と navbar で対（tree=構造地図 / flow=データフロー）

雛形: [`template/dark-explainer.html`](template/dark-explainer.html)（このスキル同梱・コピー元）。
レイヤ/データフロー/モード分岐など**込み入った図**は、雛形より上記の `layer-l4-detail.html` /
`mode-x-er-explainer.html` の `<style>` からクラスをコピーする方が速い（§4）。

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

## 4. リッチ・コンポーネント語彙と図パターン（再利用）

雛形 `template/dark-explainer.html` の基本要素に加え、**レイヤ構造 / データフロー / モード分岐 /
入れ子 box** を描く追加コンポーネントを `docs/productization/layer-l4-detail.html` と
`docs/mode-x-er/mode-x-er-explainer.html` で実証済み。すべて同じ `:root` で動くので、これらの
`<style>` から必要クラスをコピーすれば自己完結のまま使える（CDN 不要・§0 不変条件を維持）。

### 4.1 追加コンポーネント（CSS クラス）

| クラス | 用途 |
|---|---|
| `.superbox` + `.sbh`/`.sbs` | 親 box を大枠で囲み、中に sub-box/seam を**入れ子**にする |
| `.sub-nest` + `.nh` | 入れ子の枠（レイヤ帯 / 内部 sub-box 群）。`border-color` を変えてレイヤ色分け |
| `.nest-arrow`（▼） | 入れ子・帯の縦接続 |
| `.node` + `.n-box`/`.n-sub`/`.n-seam`/`.n-plugin`/`.n-demoted` | 種別色分けカード（青=box / 緑=sub / 橙=seam / 紫=plugin / 灰=demoted） |
| `.legend` + `.item`/`.sw` | 色凡例（読み手が種別を即把握） |
| `.role` | カード見出し脇の小タグ（実装あり / 未実装＝proposal 等） |
| `.transport-band` | interface 裏の実装選択帯（紫破線・`transport: hermes\|direct\|worker`） |
| `.seam-out` + `.arrow` + `.gov-chip` | box の出口 seam → 別 box への矢印 |
| `.steps` + `.step`(+`.seam`) + `.step-down` | 番号付きステップ（サイクル / 制御フロー） |
| `.flow` + `.arr` + `.lyr` | 横方向の層チェーン（L4▶L3▶… / L1▶micro-ROS▶L0）。`.lyr`=レイヤ色チップ |
| `.optline` | 等幅 1 行のデータフロー / schema 連鎖（`A → B → C`・未凍結型に `（未凍結）`） |
| `.ex`(+`.b3`/`.c`) + `.tag`/`.body` | A/B/C の対比（Option 比較・排他層） |
| `.grid2`/`.grid3` | 2/3 カラム並置 |
| `.note`/`.safe` | 補足注記 / 安全境界（赤系・「渡さない/作らせない」） |
| `.unused` | 実装スコープ「使わない」box＝暗く＋中央取消線（`opacity`+`grayscale` ＋ `::after` 横線）。個別 `.node` に付与 |

### 4.2 図パターン（recipe）

- **レイヤ・アーキ図**: `.sub-nest` をレイヤ帯にして L4→L3→L2→L1/L0 を縦積み、各帯に `.lyr`
  チップ＋`.node`/`.flow`、帯の `border-color` をレイヤ色に。例: `layer-l4-detail.html` §5.1。
- **状態の戻り（閉ループ）**: 末尾に破線 `.node n-sub`（`↩ 状態の戻り`）で戻り経路を明示し、
  1 発 pipeline でなく知覚・再計画サイクルだと示す。出典 doc の §状態の戻り を `file:line` で。
- **typed 連鎖**: stage 間の中間生成物を `.optline` で `RawModelOutput → … → Command candidate`
  と示し、未凍結の型に `（未凍結）` を付す。
- **入れ子の box**: 外 `.superbox` の中に `.sub-nest`（sub-box 群）＋ `.seam-out`（出口 seam）。
  例: `layer-l4-detail.html` §0。
- **モード/案の対比**: `.ex`(A) / `.ex.b3`(B) / `.ex.c`(C) で Option を**対称に**（利点・弱点両方）。
- **段の存在理由（"無いと"）**: 各 stage に「この段が無いと何が壊れるか」を `.s`（黄字）で添える。
  WHAT だけでなく WHY/安全根拠が伝わる（出典 doc の "…が無い場合" を `file:line` で）。
- **per-mode explainer**: 1 モードを 1 自己完結 HTML に（位置づけ→data flow→各層詳細→profile→
  gate/phase→未凍結→docs マッピング表）。例: `mode-x-er-explainer.html`。
- **実装スコープ色分け**: 使う/使わない/一部使用/optional を 1 枚で示す。「使わない」box に `.unused`
  （暗く＋中央取消線）、**一部使用**は `.role` テキストラベル＋文章で範囲明記、**optional** はラベル。
  box ごとに個別付与（図全体へグローバル適用しない）。例: `layer-l4-detail.html` §5.0 /
  `mode-x-er-explainer.html` §2。
- **ディレクトリツリー（機能ツリー）**: 「何があるか・何を採らないか」の全体地図を等幅の
  ディレクトリ型ツリー（`pre.tree`）で描く。CSS・構造規約は §5。例: `robot-architecture-tree.html`。

### 4.3 引用規約（この repo 固有・footer に明記する）

- `productization/` docs = **exact line**（`02:67-91`）。
- `architecture/` ・ `mode-*` docs = **file/§**（`mode-x-er/01 §全体像`）。同 dir 内 HTML からは
  file:line（`01:83-92`）でもよいが、ページ footer に採用規約を書く。
- コード = symbol-anchored（`action_map.py` / `safety_clamp.h`）。凍結契約の数値（`0.3 m/s` 等）は
  symbol ＋ 出典 doc:line を併記。

### 4.4 検証（提出前・§2 に加えて）

- 本文に 16 進ハードコードが無い（`grep '#[0-9a-fA-F]{6}'` が `</style>` 以降 0 件）。
- 使用クラスがすべて `<style>` に定義済み（未定義クラス 0）。内部 `href` が実在する（相対パス）。
- 大きな改訂後は **敵対検証**（citation を出典 doc:line で裏取り＋ completeness 批評）を
  かけてから「完了」とする（[docs-first.md §引用](../../rules/docs-first.md)）。

## 5. ディレクトリツリー型図解（architecture tree view）

**「何があるか・何を採らないか」の全体地図（機能 inventory）** を、ファイルシステムの
ディレクトリ構成のような等幅ツリー（`pre.tree`）で描くパターン。実証済みの完成例:
`docs/architecture/robot-architecture-tree.html`（M1 単騎 01-08 機能ツリー。**構造の参照元**。
配色のみ先行制作の独自値 — 新規ページは §カラーパレットの正準 `:root` に §5.2 の割当てで揃える）。

### 5.1 使いどころ（flow 図との分業）

- **tree = 構造の地図**: 機能領域を「番号付きディレクトリ」、実体（ノード/デバイス/トピック）を
  「ファイル」として分類・一覧する。**採らないもの（✗）を理由つきで残す**のが本質的価値。
- **flow = データの流れ**: runtime のデータフローは companion の別 HTML に分離し、`.navbar` で
  相互リンクする（例: `robot-architecture-tree.html` ①tree ↔ `perception-localization-flow.html`
  ②flow）。1 枚に両方を詰め込まない。

### 5.2 コンポーネント（正準 `:root` で駆動する tree CSS）

```css
/* tree view — §カラーパレットの :root に追記して使う */
pre.tree{font-family:"SF Mono",Menlo,Consolas,monospace;font-size:12.3px;
  line-height:1.62;white-space:pre;color:var(--ink)}
.card.tree-card{overflow-x:auto}   /* ツリーは折返さず横スクロール */
/* カテゴリ色 = 短いクラスをアクセント変数へ割当（意味は凡例 .legend で固定） */
.t1{color:var(--box)} .t2{color:var(--sub)} .t3{color:var(--seam)}
.t4{color:var(--plugin)} .t5{color:var(--teal)} .t6{color:var(--red)}
.tx{color:var(--faint);opacity:.85}       /* ✗ 不採用（理由つき残置） */
.td{color:var(--muted)}                   /* 注記・正本併記・[virtual]/[external] */
.tn{color:var(--sub);font-weight:700}     /* ★NEW/TARGET */
.tw{color:var(--yellow);font-weight:700}  /* ⚠未確定/ドリフト */
```

罫線は `├──`/`│`/`└──`。行内のデータフローは `─▶ /topic` の最小限に留める
（本格的な流れは §5.1 の flow 側へ）。

### 5.3 構造規約（レシピ）

1. **トップレベル＝番号付きディレクトリ**（例 `01_Sensors/`〜`08_Human_Interaction/`）。番号で
   読み順・レイヤ順を固定し、凡例 `.legend` の色と 1:1 に対応させる。
2. **1 行 = 1 ノード + 正本併記**: 行末 or 直下に `正本: docXX §Y`（`.td`）。file:line の
   裏取りは §0-3 どおり（記憶で書かない）。
3. **✗ 不採用を消さず残す**: `.tx` ＋理由 1 行（「なぜ無いか」も地図の情報。例:
   `robot-architecture-tree.html` の GNSS / ros2_control / micro-ROS）。
4. **状態マーカーは固定 4 種**: `★NEW/TARGET`（`.tn`）・`⚠未確定/ドリフト`（`.tw`）・
   `✗ 不採用`（`.tx`）・`[virtual]/[external]` 等の属性注記（`.td`）。凡例に必ず載せる。
5. **横断帯（cross-cutting）はツリーへ入れない**: Observability のように全ディレクトリへ跨る層は
   別カードへ出し、「run_id / gen_id 等の key join で結合」のような**関係の記述**に留める。
6. **差分表で締める**: 一般形（教科書構成）との差分を `一般形 / 実構成での裁定 / 正本` の
   3 列表で末尾にまとめる（「なぜこの形か」を残す）。
7. **`.navbar` を冒頭に**: companion（flow・詳細カタログ）への相互リンク。現在頁は
   `class="here"` で示す。

### 5.4 検証（§2/§4.4 に加えて）

- `white-space:pre` ゆえ**罫線の桁揃え**を目視確認（等幅で崩れない・折返し無し）。
- ツリーを含むカードに `overflow-x:auto`（狭幅では横スクロールにする）。

## References
- 同梱雛形: [`template/dark-explainer.html`](template/dark-explainer.html)
- 完成例: `docs/productization/box-taxonomy.html` / `docs/productization/layer-l4-detail.html`（リッチ語彙の参照元）/ `docs/productization/layer-l3-detail.html` / `docs/mode-x-er/mode-x-er-explainer.html`（per-mode explainer）/ `docs/architecture/robot-architecture-tree.html`（tree view・§5）
- リッチ・コンポーネント語彙と図パターン: 本書 §4 ／ ディレクトリツリー型: 本書 §5
- 正本ルール: [docs-first.md](../../rules/docs-first.md) / [consistency-check.md](../../rules/consistency-check.md)
- Codex 版: `.agents/skills/html-explainer/SKILL.md`（同手順の簡約英語版・同じ雛形を参照）
