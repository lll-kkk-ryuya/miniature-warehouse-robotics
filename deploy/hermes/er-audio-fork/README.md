# ER native-audio Hermes overlay (`er-audio-fork`)

> **設計正本**: [PR #355（docs・MERGED `3002fd8`）](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/pull/355) / [`docs/mode-x-er/06-unfrozen-contract-resolutions.md` §5 + §5 補遺（:263-271）](../../../docs/mode-x-er/06-unfrozen-contract-resolutions.md) / [issue #356](https://github.com/lll-kkk-ryuya/miniature-warehouse-robotics/issues/356)
> ✅ **依存（解消済み）**: 本パッケージが productionize する forked-Hermes-200 の記録（"default = Hermes for audio" の TARGET）は docs PR **#355 が main に land 済み（`3002fd8`）**で、**merged doc06 §5 補遺（:263-271）に着地済み**。補遺は「UNFORKED Hermes = 400（PROBE-2 不変）／ fork ありなら audio を Hermes に乗せられる（TARGET）／ **CURRENT = 音声 direct のまま（恒久 fallback）**」と honest に精緻化（:269）。よって本パッケージの設計正本リンクは merged main 上で解決する。
> 本書は **transport/input レイヤの overlay** のみを扱う。orchestration / safety は一切触らない（下記「触らないもの」）。

このディレクトリは、Hermes Gateway（hermes-agent v0.15.1）の OpenAI 互換
`/v1/chat/completions` が **OpenAI `input_audio` content part を受理し、Gemini native
`inlineData{ mimeType: audio/wav }` にマップする**ようにする 2 ファイルパッチ（overlay）と、
それを **personal な `~/.hermes` を一切触らずに** 当てて動かすための applier を収める。

**transport/input レイヤ（commit 1・本 PR の主成果物 `er-audio-fork/`）**:

| ファイル | 役割 |
|---|---|
| `0001-input_audio-passthrough.patch` | 2 ファイルパッチ本体（`gateway/platforms/api_server.py` + `agent/gemini_native_adapter.py`） |
| `apply-fork.sh` | 冪等な applier（`--check` dry-run / `--revert` 対応・personal clone を in-place で触ることを拒否） |
| `run-er-gateway.sh` | **one-shot ランチャ**（隔離 worktree 作成 → patch 適用 → lean ER gateway 起動を一気通貫。`--probe`/`--stop`。langfuse は **載せない**＝plugin は fail-open no-op） |
| `.env.example` | secrets ひな型（placeholder のみ・`GOOGLE_API_KEY` / `API_SERVER_KEY` / port） |
| `UPSTREAM-PR.md` | `NousResearch/hermes-agent` 向け upstream PR 草案（**NOT SUBMITTED**＝外向き承認ゲート） |
| `TRANSPORT-FLIP-PLAN.md` | ER audio leg を default-Hermes に flip する **design-only** プラン（コードは `feat/mode-x-er` 側） |
| `README.md` | 本書 |

**観測（commit 2・別 concern・`hlf-g0-langfuse/` ＝ Langfuse trace 所有の探索。詳細は [`hlf-g0-langfuse/README-hlf-g0.md`](hlf-g0-langfuse/README-hlf-g0.md)）**:

> ⚠️ これは **trace 所有レイヤの opt-in scaffolding** で、input/transport とは**別 concern**。`run-er-gateway-langfuse.sh` だけが消費し、shipped default の trace owner は **Bridge-owned 継続**（doc06:9 / doc02:179・下記「現状（honest status）」§観測 の fence 参照）。`config.lean.yaml` の `plugins.enabled` は Option D の root-cause 退行を防ぐために残す。

| ファイル | 役割 |
|---|---|
| `hlf-g0-langfuse/run-er-gateway-langfuse.sh` | `run-er-gateway.sh` を source し、隔離 langfuse install ＋ plugin ON を足す launcher |
| `hlf-g0-langfuse/run-hlf-g0.sh` + `hlf_g0_probe.py` | HLF-G0 live probe（human-gated・PASS/FAIL/INCONCLUSIVE 判定） |
| `hlf-g0-langfuse/probe-hlf-g0.sh` | design-only scaffold（gate チェックリスト印字・live は placeholder） |
| `hlf-g0-langfuse/README-hlf-g0.md` / `PLUGIN-TRACEID-ANALYSIS.md` / `WRAPPER-REMOVAL-PLAN.md` | 分析・予測・Pattern B 設計（design-only） |
| `hlf-g0-langfuse/RESULT.md` | live 結果の転記先（Option D は #360 spike で live 観測済＝下記） |
| `hlf-g0-langfuse/.env.example` | Langfuse 用 secrets ひな型（placeholder のみ） |

そして本 overlay の lean config 正本 [`deploy/dev/hermes-er/config.lean.yaml`](../../dev/hermes-er/config.lean.yaml)（commit 2 で plugin ON 化・上記 fence 参照）。

---

## このフォークが何をするか / なぜ必要か

- **何を**: OpenAI 互換 chat content part `{"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}`
  を Hermes が受理し、native Gemini 側に `inlineData{ mimeType: "audio/<format>", data: <base64> }` として渡す。
  これは **入力 MODALITY を 1 つ足すだけ**の transport-only 変更（image_url passthrough と同型）。
- **なぜ**: **unforked の Hermes は audio を運べない**。専用 lean Hermes gateway の
  `/v1/chat/completions` に `input_audio` を POST すると **HTTP 400 `unsupported_content_type`**
  （メッセージ: `Unsupported content part type 'input_audio'. Only text and image_url/input_image parts are supported.`）。
  これは **PROBE-2 として 2026-06-27 に実測確定**（[06 §5:159 PROBE-2](../../../docs/mode-x-er/06-unfrozen-contract-resolutions.md)）。
  Gemini Robotics-ER 自体は audio 入力を native サポートするため、欠けているのは
  「Hermes が OpenAI `input_audio` part を Gemini へ透過する」最後の 1 段だけ。この overlay がその段を足す。
- **位置づけ**: これは **productionization seam**。`docs/mode-x-er/06 §5 補遺`（:263-271・特に :269）の
  *"fork ありなら audio を Hermes default target にできる"* は **この overlay が deploy されて初めて到達できる TARGET**。
  **deploy されるまでは現行の音声 transport は `direct`（ER へ直送）が恒久 fallback** であり、
  本フォークは「ER 音声を direct ではなく uniform Hermes transport に載せ替える」ための前提部品。

### 触らないもの（transport/input layer のみ・本フォークの不変条件）

本フォークは **入力 modality の透過のみ**で、以下には一切手を入れない:

- `action_map` の idempotency mint（冪等な action 採番）
- Policy Gate
- timeout 時の 0-dispatch（タイムアウトで何も実機投入しない契約）
- eval_sdk の outcome scores（result / SR / SPL / collision / deadlock）

これらは orchestration / safety 層であり、本パッチの diff（2 ファイル・content-part 正規化のみ）には現れない。

---

## ライブ実証結果（2026-06-27・grounded）

専用 lean ER gateway（下記）に対して `input_audio` を POST した実測:

- **HTTP 200**。ER は **native audio** を理解した（音声に含まれる語の transcript のみで応答＝
  音声を実際に聴いて処理している）。
- lean 経路の latency 中央値 **3.69s** vs direct **4.24s**（n=4・comparable・ER-thinking の交絡あり）。
- 1 call あたり **+約 408 prompt tokens**（Hermes 経由の overhead）。

> **注意（multi-provider 比較）**: Hermes は **server-side で単一 active model**（per-request の
> provider routing は無い）。4-provider 比較を回すには **provider ごとに gateway を分ける**
> （config 切替 + 再起動）必要がある。本フォーク 1 つでは ER（Gemini native）1 系統のみ。

> **ER audio = Gemini-native 経路 限定**: パッチの adapter hunk は **Gemini *native* adapter**
> （`agent/gemini_native_adapter.py`）だけに `input_audio`→`inlineData` のマッピングを足す。
> 第2の Gemini 経路 `agent/gemini_cloudcode_adapter.py` は `input_audio` を **drop** する
> （`_coerce_content_to_text` が `image_url`/`input_audio` を `logger.debug("Dropping multimodal
> part …")` で落とす＝debug ログのみで実質 silent。verified against v0.15.1 source）。shipped
> lean config は provider `google`＝native なので**到達しない**が、cloudcode provider を使う
> gateway では audio が落ちる footgun。本 productionization は **native provider 前提**。

---

## 適用方法

### 一気通貫（one-shot ランチャ・推奨）

手順を手で踏まずに `run-er-gateway.sh` 1 本で「隔離 worktree 作成 → patch 適用 → lean ER gateway 起動」まで通る。**初回は lean home の `config.yaml` + `.env` が必要**（下記「初期化」）:

```bash
cd deploy/hermes/er-audio-fork

# 0) 初期化（初回のみ）: FORK ER home に config と secrets を置く（unforked とは別 home）
#    config.yaml = config.lean.yaml をコピー、.env = .env.example を埋める。
#    ⚠️ unforked run-er-hermes.sh は別 home（~/.hermes-mwr-er-lean・port 8643）を seed する。混同しない。
mkdir -p ~/.hermes-mwr-er-fork
cp ../../dev/hermes-er/config.lean.yaml ~/.hermes-mwr-er-fork/config.yaml
cp .env.example                          ~/.hermes-mwr-er-fork/.env   # then fill GOOGLE_API_KEY / API_SERVER_KEY

# 1) 起動（fg）: 隔離 worktree + patch + lean gateway を一気通貫で
./run-er-gateway.sh

# 起動 + input_audio が HTTP 200 を返すか自己検証（bg のまま残す）:
./run-er-gateway.sh --probe
# 停止 + 隔離 worktree 削除:
./run-er-gateway.sh --stop
```

ポート/HOME などは env で可変（`PORT` 既定 `8644`・`HERMES_HOME` 既定 `~/.hermes-mwr-er-fork`・`HERMES_SRC` 既定 `~/.hermes/hermes-agent`）。**secrets は `$HERMES_HOME/.env` を source するのみ・値は非表示**。`run-er-gateway.sh` は **langfuse を PYTHONPATH に載せない**（plugin は fail-open no-op）＝観測を足したい HLF-G0 探索は `hlf-g0-langfuse/run-er-gateway-langfuse.sh`。

> **標準 / fallback の集約 ＋ home 分離（既存 `deploy/dev/run-er-hermes.sh` との関係）**: **`run-er-gateway.sh`（port `8644`・fork 適用・text+image+`input_audio` の全 modality・**専用 home `~/.hermes-mwr-er-fork`**）が SHIPPED STANDARD ER gateway**。`run-er-hermes.sh`（port `8643`・非 fork・text/image のみ・home `~/.hermes-mwr-er-lean`）は **deprecated / fork-free fallback**（audio 不要時のみ）に格下げ済み（同 launcher の EOF banner 参照）。**両者は別 home なので互いの `.env` を継承せず衝突しない**（旧仕様は同一 home 共有で、fork が unforked の `.env` の `API_SERVER_PORT=8643` を継承し `8644` の代わりに **8643 を bind して衝突した**＝2026-07-03 実測）。加えて `run-er-gateway.sh` の `ensure_env_port()` が起動時に自 home の `.env` を **8644 に再固定**する（gateway は `.env` を auto-load し `.env` 値が launcher default に勝つため・`gateway/run.py:851-852`）。Bridge は標準で `8644` に繋ぐ。HOME override 変数も非対称（`run-er-hermes.sh` は `MWR_ER_HERMES_HOME`）なので、env を渡すときは各 launcher の USAGE ヘッダのポート/HOME 変数名を確認する。

### 手動（isolated worktree + PYTHONPATH override・ランチャを使わない場合）

### 鉄則（SAFETY）

- **`~/.hermes/hermes-agent`（personal daily-driver clone・port 8642・memory ON）は絶対に
  in-place で patch / checkout-branch しない。** これは利用者の openai-codex daily-driver。
- パッチを当ててよいのは **personal clone の ISOLATED worktree**（または別 clone）だけ。
  `apply-fork.sh` は `HERMES_SRC` が personal clone を指すと **REFUSE する**。

### 手順

```bash
# 0) 変数（このディレクトリへの絶対パス）
FORK_DIR=/Users/<you>/Developer/mwr-hermes-er-fork/deploy/hermes/er-audio-fork
PERSONAL=~/.hermes/hermes-agent          # personal clone — 触らない
SRC=/tmp/hermes-er-fork                   # 隔離 worktree の置き場（gitignore 対象・任意）

# 1) personal clone の ISOLATED worktree を作る（venv/node_modules はコピーされない＝軽量）
git -C "$PERSONAL" worktree add "$SRC" -b mwr-er-audio HEAD

# 2) パッチを当てる（冪等・先に git apply --check の dry-run を内部で実行）
HERMES_SRC="$SRC" "$FORK_DIR/apply-fork.sh"
#   事前確認だけ:  HERMES_SRC="$SRC" "$FORK_DIR/apply-fork.sh" --check
#   元に戻す:      HERMES_SRC="$SRC" "$FORK_DIR/apply-fork.sh" --revert

# 3) patched モジュールを起動する（PYTHONPATH override で personal venv の依存を再利用）
#    PYTHONPATH が editable finder を上書きし、patched モジュールが <SRC> からロードされる。
#    personal の source は読み取りすらしない（worktree 側が勝つ）。
PYTHONPATH="$SRC" "$PERSONAL/venv/bin/python" -m hermes_cli.main gateway run --accept-hooks
```

**PYTHONPATH override の要点（実証済み）**: `PYTHONPATH=<SRC>` は editable（PEP660）install の
finder より優先される。patched な `gateway/platforms/api_server.py` /
`agent/gemini_native_adapter.py` は `<SRC>` からロードされる一方、依存（venv の site-packages）は
**personal venv をそのまま再利用**する。＝personal の source を 1 行も変えずに patched code を走らせられる。

### 隔離 worktree の片付け

```bash
git -C "$PERSONAL" worktree remove "$SRC"     # 未コミット残があると拒否される
git -C "$PERSONAL" branch -D mwr-er-audio      # 使い捨てブランチ
git -C "$PERSONAL" worktree prune
```

---

## lean ER gateway の構成（実証済み・参考）

この overlay は **専用 lean Hermes gateway** と組み合わせて使う（personal `~/.hermes` とは別 `HERMES_HOME`）。
構成の正本は同じ worktree の [`deploy/dev/hermes-er/config.lean.yaml`](../../dev/hermes-er/config.lean.yaml) と
launcher [`deploy/dev/run-er-hermes.sh`](../../dev/run-er-hermes.sh)、secrets ひな型は
[`deploy/dev/hermes-er/.env.example`](../../dev/hermes-er/.env.example)。要点:

| 項目 | 値 | 出所 |
|---|---|---|
| provider | `google`（native Gemini） | `config.lean.yaml` |
| model | `gemini-robotics-er-1.6-preview` | `config.lean.yaml`（API server は request の `model` を無視し server-side 固定） |
| `platform_toolsets.api_server` | `[]`（明示空＝**0 tools**。unset は 35 tools default） | `config.lean.yaml` |
| memory | off（`memory_enabled: false` / `user_profile_enabled: false`） | `config.lean.yaml` |
| API server port | `8644`（**personal 8642 とも unforked fallback 8643 とも分離**・専用 home `~/.hermes-mwr-er-fork`・`ensure_env_port()` が起動時 8644 に再固定。`PORT`/`API_SERVER_PORT` で可変） | `.env.example` / `run-er-gateway.sh` |
| secrets | `HERMES_HOME/.env` の `GOOGLE_API_KEY` + `API_SERVER_KEY` + `API_SERVER_HOST/PORT` | `.env.example` |

> **secrets 規約**: 値は `HERMES_HOME/.env`（gitignore 対象）に置き、**スクリプトは `.env` を SOURCE する。
> 値を echo / print しない**（[safety.md](../../../.claude/rules/safety.md) / [environments.md](../../../.claude/rules/environments.md)）。
> repo に置くのは `.env.example`（プレースホルダのみ）。
>
> 実証ラン（grounded fact・2026-06-27）は当時 instance home `~/.hermes-mwr-er-lean` で port **8644**
> だった（当時は fork も lean home を共用）。**現在の shipped default は fork 専用 home
> `~/.hermes-mwr-er-fork`（port 8644）**で unforked（8643・lean home）と分離。port は常に env で parameterize する。

---

## MAINTENANCE（保守戦略）

このフォークは **薄い overlay**（2 ファイル・transport のみ）として「upstream に追従しやすく・退役しやすい」形を保つ。

1. **upstream bump 時の再適用**: hermes-agent の version が上がったら、まず
   `apply-fork.sh --check`（内部で `git apply --check` dry-run）で当たるか確認する。
   - 当たれば（exit 0）そのまま `apply-fork.sh` で適用。
   - 当たらなければ（exit 3＝context drift）、新 version 上で 2 ファイルを手で当て直し、
     `git diff` で **パッチを再 cut** する（hunk offset / context を更新）。パッチ先頭の
     `index <old>..<new>` blob hash は当たり判定に使われないので、context が一致すれば良い。
   - `apply-fork.sh` は target が **hermes-agent v0.15.1** であることを `pyproject`（name+version）
     または 2 target ファイルの存在で検証する。version を上げる際はこのガードも追従させる
     （applier 冒頭の `verify_version` の `0.15.1` 期待値）。
2. **overlay の退役を優先**: 長期的には **upstream への PR**（OpenAI `input_audio` part の
   native 透過）を出して overlay を不要化するのが望ましい。overlay は「upstream が受け入れるまでの
   橋渡し」であり、恒久 fork を意図しない。退役できれば `direct` ではなく Hermes 経由を
   標準にする TARGET（06 §5 + #355 の §5 補遺）が本家機能で満たされる。
3. **冪等性**: `apply-fork.sh` は適用済み（`_AUDIO_PART_TYPES` マーカー検出）なら no-op。
   `--revert` で安全に剥がせる（reverse `--check` を先に通すので drift があれば拒否）。

---

## SAFETY（再掲・最重要）

- **personal `~/.hermes/hermes-agent` を in-place で patch / checkout-branch しない。**
  必ず **isolated worktree（または別 clone）**に当てる。`apply-fork.sh` は personal clone を
  指す `HERMES_SRC` を **拒否**する（多層防御）。
- **secrets を echo / print / commit しない。** `.env` は SOURCE するのみ・repo には `.env.example` のみ。
- 本フォークは **transport/input layer 限定**。orchestration / safety
  （action_map idempotency / Policy Gate / 0-dispatch-on-timeout / eval_sdk scores）は不変。

---

## 現状（honest status）

- **検証済み（2026-06-27 live）**: パッチ適用 → lean ER gateway 起動 → `input_audio` POST →
  **HTTP 200・ER が native audio を理解**（上記「ライブ実証結果」）。
- **未出荷（not yet shipped）**: 本 overlay の deploy（Bridge の default を audio=Hermes に切替）は
  **未実施**。それまで **音声 transport の default は `direct`**（恒久 fallback）。
  *"default = Hermes for audio"* は本フォークが productionize する **TARGET**。
- **applier の自己検証範囲**: `apply-fork.sh` の guard / 冪等 / `--check` / `--revert` の分岐は検証済み。
  実 v0.15.1 source への clean-apply は `git apply --check` が通ることを確認済み
  （personal clone の sandbox 制約上、clean-apply→revert の往復はホスト側の隔離 worktree で実走して確認すること）。
- **hardening パッチ整合の offline 検証（2026-07-01・grounded）**: hardening 追加後の
  `0001-input_audio-passthrough.patch` を、実 v0.15.1 の 2 target ファイル（personal clone の
  `api_server.py` / `gemini_native_adapter.py`）を**使い捨て tmp repo にコピーした複製**へ
  `git apply --check` → apply → `--reverse --check` の往復が clean に通ることを確認済み
  （**personal clone は in-place で一切触らず・gateway 未起動・network 無し**）。適用後の
  `api_server.py` は `py_compile` OK、ruff は base と同数で **hardening 分岐に新規 finding ゼロ**
  （`import base64` の local import は module import block を触らない意図的 tradeoff）。分岐ロジックは
  単体シミュレーションで 4 ケース（valid wav 受理／未知 format 400／非 base64 400／oversize 400）
  PASS。**ただし実 gateway への live apply-check・実 HTTP 4 ケースは OPERATOR-GATED**（下記 deferred）。
- **productionization hardening（#356 DoD・`api_server.py` `_normalize_multimodal_content`
  の `input_audio` ACCEPT 分岐に additive 実装済み）**: パッチは現状 `data` の非空検証・`format`
  欠落時 `wav` default に加え、以下 3 つの guard を持つ。いずれも受理前に `ValueError`
  （`invalid_content_part:...` スタイル）で 400 を返し、他 modality（text/image）と error path を揃える:
  - **base64 妥当性**: `base64.b64decode(data, validate=True)` を try/except で実行し、strict
    に decode できない `data` を reject（従来は非空 str であれば素通しだった）。`base64` は
    v0.15.1 の module import block に無いため、**分岐内で local import**（`import base64 as
    _mwr_base64`）してパッチを最小・再適用可能に保つ。
  - **サイズ上限**: **gateway 既存の module-level 定数 `MAX_REQUEST_BYTES`（= `10_000_000` /
    10 MB・`api_server.py` に既存）を再利用**し、decode 後 audio bytes がこれを超えたら reject。
    数値を発明せず既存 ceiling を流用（raw base64 body 側は既存 Content-Length middleware も
    同定数で弾く）。**この定数の名前/場所は隔離 Hermes v0.15.1 に対する live apply-check で
    最終確認する**（下記「未検証／OPERATOR-GATED」）。
  - **format allowlist（WAV-ONLY first）**: `_AUDIO_FORMAT_ALLOWLIST = {"wav"}`
    を新設し、`wav` 以外の `format` を reject（従来は任意 `format` が `audio/<fmt>` に素通しされ
    provider 4xx で初めて弾かれた）。**保守的に wav 一択から始める**＝live 実証（2026-06-27）も
    probe（`run-er-gateway.sh` の say+afconvert）も wav であり、実証済み経路のみを許可する。
    **より広い Gemini audio mime allowlist への拡張（mp3/m4a/aac/ogg/flac 等）は follow-up**
    （Gemini audio の対応 mime-type docs に対して確定してから追加する・verified とは主張しない）。
    過不足は remaining[]（confirm-value）扱い。
- **cloudcode silent-drop（footgun・意図的に未対応）**: Gemini **cloudcode** adapter
  （`agent/gemini_cloudcode_adapter.py`）は `input_audio` を **silent に drop** する
  （`_coerce_content_to_text` が `logger.debug("Dropping multimodal part …")` で落とす＝実質無音）。
  本 hardening は **native adapter のみ**を対象とし、**cloudcode は patch しない**。shipped lean
  config は provider `google`＝native なので到達しないが、cloudcode provider を使う gateway では
  audio が **エラー無く消える**。この経路を使う場合は adapter 側の別対応が要る（本 overlay の
  責務外・上記「ライブ実証結果」の fence / `UPSTREAM-PR.md` §Non-goals と整合）。
- **依然 deferred / OPERATOR-GATED（live human gate）**: 上記 3 guard は offline のパッチ整合
  （`git apply --check`／再適用往復・compile・ruff・分岐ロジックの単体シミュレーション）まで検証済み
  だが、**隔離 Hermes v0.15.1 gateway への live apply-check と 4 実挙動ケース**
  （valid wav → 200 ／ 未知 format → 400 ／ 非 base64 → 400 ／ oversize → 400）は
  **operator 実走の human gate**（COST GUARD 下では live/provider/network を叩かない）。
  `MAX_REQUEST_BYTES` の定数名/場所も同 gate で最終確定する。
- **観測（Langfuse trace 所有・commit 2 / `hlf-g0-langfuse/`）の検証状態（誇張回避のため厳密に）**:
  - **live 観測済み（#360 spike）**: 本 package の `run-er-gateway-langfuse.sh`（plugin ON）経由で audio が
    HTTP 200 で ER に届き、**plugin が trace を `create_trace_id(seed="H::H")` の決定的位置に着地**させる
    （**Option D / predict-seed**）ことを #360（`spike/langfuse-plugin-d/verify_d_audio.py`）が **live PASS**
    （観測 trace = `d1477eef…`）。＝「plugin-owned trace は実体として観測できる」は **検証済み**。
  - **未検証（human-gate のまま）**: ① 本 package 内の **literal HLF-G0 probe**（`hlf-g0-langfuse/`・
    *inbound* `trace_id` を plugin が honor するか）は **未実走**（`RESULT.md` 参照）。静的解析の予測は
    **stock では FAIL**（plugin は trace_id を自生成・inbound を読まない＝`PLUGIN-TRACEID-ANALYSIS.md`）で、
    Option D は「inbound honor」ではなく「seed 一致で再導出」という別解。② #6 scorer 脚まで含む
    **end-to-end score-join の live 実証**は #360 でも human-gate（#360 review 参照）。
  - したがって **shipped default の trace owner は Bridge-owned 継続**（doc06:9 / doc02:179）。
    `config.lean.yaml` の `plugins.enabled` は **Option D 探索用 opt-in scaffolding** で、消費するのは
    `run-er-gateway-langfuse.sh` のみ（base `run-er-gateway.sh` は langfuse を載せず fail-open no-op）。

## 関連（cross-link）

- **live 実走の turnkey 手順**: 本フォークの gateway を起動して ER→L3→Langfuse を実機材で回す
  operator 手順（preflight・課金 human-gate・scoped 承認文言・honest limits）は
  [`docs/dev/07-mode-x-er-live-e2e-runbook.md`](../../../docs/dev/07-mode-x-er-live-e2e-runbook.md)
  を正本にする。turnkey な起動順は同 runbook **§1「Turnkey live steps」**、本フォーク経由で
  audio leg を Hermes plugin に観測させる Option-D 経路は同 runbook **§2.5「Option-D」**（どちらも
  live は human-gate）。設計索引は [`docs/mode-x-er/README.md` §Transport (index)](../../../docs/mode-x-er/README.md)
  にも本フォークと runbook の両行がある。
- **transport の default 切替の着地状況（honest）**: **#389 の adapter live-send 機構
  （`build_provider_request`/`ErTransportSender`/`_live_send`, `gemini_er.py:87/154/222`）は main 着地済
  （ad563de）**。ただし audio=Hermes を **shipped default にする productionize は依然 未 ship**
  （上記「現状（honest status）」§未出荷）＝残るは (a) 稼働 Bridge cycle が `propose_plan` を呼ぶ wiring
  （XER6）と (b) 本 8644 fork の default 配備。それまで **音声 transport の default は `direct`**（恒久 fallback）。
