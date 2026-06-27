# ER native-audio Hermes overlay (`er-audio-fork`)

> **設計正本**: [PR #355（docs）](https://github.com/) / [`docs/mode-x-er/06-unfrozen-contract-resolutions.md` §5 + §5 補遺](../../../docs/mode-x-er/06-unfrozen-contract-resolutions.md) / [issue #356](https://github.com/)
> 本書は **transport/input レイヤの overlay** のみを扱う。orchestration / safety は一切触らない（下記「触らないもの」）。

このディレクトリは、Hermes Gateway（hermes-agent v0.15.1）の OpenAI 互換
`/v1/chat/completions` が **OpenAI `input_audio` content part を受理し、Gemini native
`inlineData{ mimeType: audio/wav }` にマップする**ようにする 2 ファイルパッチ（overlay）と、
それを **personal な `~/.hermes` を一切触らずに** 当てて動かすための applier を収める。

| ファイル | 役割 |
|---|---|
| `0001-input_audio-passthrough.patch` | 2 ファイルパッチ本体（`gateway/platforms/api_server.py` + `agent/gemini_native_adapter.py`） |
| `apply-fork.sh` | 冪等な applier（`--check` dry-run / `--revert` 対応・personal clone を in-place で触ることを拒否） |
| `README.md` | 本書 |

---

## このフォークが何をするか / なぜ必要か

- **何を**: OpenAI 互換 chat content part `{"type":"input_audio","input_audio":{"data":"<base64>","format":"wav"}}`
  を Hermes が受理し、native Gemini 側に `inlineData{ mimeType: "audio/<format>", data: <base64> }` として渡す。
  これは **入力 MODALITY を 1 つ足すだけ**の transport-only 変更（image_url passthrough と同型）。
- **なぜ**: **unforked の Hermes は audio を運べない**。専用 lean Hermes gateway の
  `/v1/chat/completions` に `input_audio` を POST すると **HTTP 400 `unsupported_content_type`**
  （メッセージ: `Unsupported content part type 'input_audio'. Only text and image_url/input_image parts are supported.`）。
  これは **PROBE-2 として 2026-06-27 に実測確定**（[06 §5 補遺 PROBE-2](../../../docs/mode-x-er/06-unfrozen-contract-resolutions.md)）。
  Gemini Robotics-ER 自体は audio 入力を native サポートするため、欠けているのは
  「Hermes が OpenAI `input_audio` part を Gemini へ透過する」最後の 1 段だけ。この overlay がその段を足す。
- **位置づけ**: これは **productionization seam**。`docs/mode-x-er/06 §5 補遺` の
  *"default = Hermes for audio"* は **この overlay が deploy されて初めて到達できる TARGET**。
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

---

## 適用方法（isolated worktree + PYTHONPATH override）

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
| API server port | `8644`（**personal の 8642 と分離**。`API_SERVER_PORT` / `MWR_ER_HERMES_PORT` で可変） | `.env.example` / `run-er-gateway.sh` |
| secrets | `HERMES_HOME/.env` の `GOOGLE_API_KEY` + `API_SERVER_KEY` + `API_SERVER_HOST/PORT` | `.env.example` |

> **secrets 規約**: 値は `HERMES_HOME/.env`（gitignore 対象）に置き、**スクリプトは `.env` を SOURCE する。
> 値を echo / print しない**（[safety.md](../../../.claude/rules/safety.md) / [environments.md](../../../.claude/rules/environments.md)）。
> repo に置くのは `.env.example`（プレースホルダのみ）。
>
> 実証ラン（grounded fact）は instance home `~/.hermes-mwr-er-lean` で port **8644** だった
> （`API_SERVER_PORT` 可変ゆえ instance によって port が変わりうる）。本書は repo default の
> 8644 を一次値とし、port は常に env で parameterize する。

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
   標準にする TARGET（06 §5 補遺）が本家機能で満たされる。
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
