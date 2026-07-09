[worktree: mwr-xer6-live-matrix | branch: feat/mode-x-er-live-matrix | track: mode-x-er]

## 何を / なぜ

XER6 live matrix に **合成俯瞰画像** の経路を足す。text-only の live ER はカメラを持たず、Visual
Resolver が snap できない pixel を発明して `empty_command` になる。frozen `dev-sim-v1` calibration
（snap_radius 0.25 m）と整合する 1000×1000 の俯瞰 PNG を生成し、live ER の request に
`overhead_image_ref` として添付することで、`--pixel-hints` なしで ER が実際に知覚し plan が自然に
resolve するようにする。**live 送信は行わない**（後で main/operator session が実行）。本 PR は
wiring + 0-charge の offline selftest + assertion tier + live 配分案のみ。

- `gen_overhead_image.py`（新規）: 純 stdlib（`zlib`+`struct`、Pillow 不在）で決定的 PNG を生成。
  positive = RED@(420,310)→shelf_1 / BLUE@(810,280)→shelf_2。`--negative` = 全 known location から
  snap_radius 超の pixel に箱を置く（コードで計算し assert）。geometry 定数は landed fixture から
  import（再発明しない）。箱は対称スパンで **描画重心 = ラベル pixel**。
- `harness.py`（改変）: `--image` / `--image-mode {positive,negative}` / `--selftest-image` を追加。
  `--image` は live request に `overhead_image_ref` をセットし、file-reader `BlobLoader` を
  `build_er_adapter(load_blob=...)` に注入（offline は fixed envelope を replay するので無影響）。
  positive tier = dispatch または `empty_command` で PASS、それ以外は distinct **⚠ WARN**（silent ✓
  にしない）。negative tier = 両 cycle で 0 dispatch のみ PASS（fail-closed）。新 flag 不在時は
  byte-compatible。
- `TIER2_PLAN.md`（新規）: operator 承認済み ≤8-call batch の配分案。

## 影響範囲

- 編集は **`spike/xer6-live-matrix/` 配下のみ**（`harness.py` 改変、`gen_overhead_image.py` /
  `TIER2_PLAN.md` / `PR_BODY_B.md` 新規）。
- **production コードは無編集**: `gemini_er.build_provider_request` の hermes image branch
  (`data:image/png`)・`adapter_factory.build_er_adapter(load_blob=)`・`er_task.overhead_image_ref`
  は landed 契約を **consume するだけ**（`warehouse_interfaces` / adapter / enums に変更なし）。
- 生成 PNG は `out/images/`（`.gitignore` 済）に runtime 生成、commit しない。

## 設計正本（docs/ リンク）

- `docs/dev/07-mode-x-er-live-e2e-runbook.md`（§4.5 cost/scoped 承認ゲート・live 手順・budget）。
- `docs/adr/0002-er-in-hermes-standard.md`（ER = Hermes 標準、fork gateway 8644）。
- `tests/unit/x_er_fixtures.py:93-101`（HOMOGRAPHY / VALID_POLYGON / SNAP_RADIUS_M = geometry 正本）、
  `:105-115`（BASE_LOCATIONS）。image branch = `.../robotics/adapters/gemini_er.py:135-139`、
  BlobLoader = `gemini_er.py:47`、`build_er_adapter` = `adapter_factory.py:77-83`。

## テスト（offline gate 出力）

`arch -arm64 <repo>/.venv/bin/python`（sandbox は Rosetta、pydantic_core は arm64 のため）で実行:

- `./run-live-matrix.sh --offline` = **15/15**（`['A✓'×3,'B_in✓'×3,'B_out✓'×3,'C✓'×3,'D✓'×3]`）、
  `live sends: 0`、exit 0（新 flag 不在時は baseline と byte-compatible）。
- `--selftest-image` = **PASS**（`data:image/png` part が生成 PNG 13529 bytes に base64 復号一致、
  network なし）。
- `--selftest-budget` = **PASS**（cap=3 で cut off、real sends=3）。
- ruff check + format = **clean**（`harness.py` / `gen_overhead_image.py`）。
- 決定性: 2 回生成で sha256 一致。painted 重心 = ラベル pixel（decode 検証で (420,310) 一致）。
- negative mutation-sensitivity: offline replay は必ず dispatch するので `--image-mode negative`
  offline は **exit 1（RED）** = fail-closed が生きている証明。

## Cost / budget（≤8-call 配分）

- **positive ×3**（variant `B_in`, `overhead_positive.png`, pixel-hints なし, FreshState on）= 3 送信。
- **negative ×2**（variant `A`, `overhead_negative.png`）= 2 送信。
- **reserve 3**: cycle ごとの追加送信は `hermes`→`direct` fallback 1 回のみ（retry loop なし・
  BudgetedSender が計上）。**hard ceiling = 8**（cap 5 + cap 3 の 2 ledger）。cap は hard STOP で
  完了保証ではない。
- per-call image ~4.5k tokens。任意 `--image` は `MAX_IMAGE_BYTES`(128 KiB ≈ ~43k tokens) で
  **サイズ上限を強制**（送信数だけでなく per-call cost もガード）。

## Residuals（正直）

- **live 送信はこの PR では一切行わない**（設計上）。positive dispatch / negative 0-dispatch は
  コードで assert 済だが、実挙動は operator が cost gate を armed にして走らせて初めて検証される。
- negative test の妥当性は ER が **画像に detection を grounding する**前提に依存。transcript から
  shelf pixel を捏造すると dispatch して FAIL しうる（それ自体が正直な findings）。
- `--image`/`--image-mode` の不一致は **soft WARN のみ**（非ブロッキング）。
- `arch -arm64` prefix は sandbox（Rosetta x86_64 shell vs arm64 pydantic_core）の artifact。
  operator の native arm64 shell では `./run-live-matrix.sh` を prefix なしで実行できる。

Note: live runs are executed later by the main session.
