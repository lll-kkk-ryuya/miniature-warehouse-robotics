# ER 入力モダリティと STT の要否（ER 単体時の Fusion 必要性を含む）

作成日: 2026-06-23

> **状態**: 設計提案 / 調査メモ。本書は外部 model（Gemini Robotics-ER）の公開仕様の確認結果と、それが Mode X-ER の data flow・Fusion box の要否に与える含意を記録する。ROS topic / REST API / config key / `warehouse_interfaces` frozen contract を追加するものではない。実装前に offline probe と契約 PR で確定する。

## 結論

1. **Gemini Robotics-ER 1.6 Preview は音声（audio）を直接入力できる**（一次情報、§1）。したがって **STT を ER の必須上流ステージにしない**。STT は optional（fallback / audit / 非 ER 経路向け）として持つ（§2）。
2. data flow 上「`STT -> Input Context -> ER`」を必須直列にしない。Input Context は `instruction_audio_ref` を第一級入力として持ち、`transcript` は optional（§3）。既存 docs も既に optional 表記であり（`docs/mode-x-er/01-architecture-and-flow.md:21`・`docs/mode-x-er/01-architecture-and-flow.md:119`・`docs/mode-x-er/README.md:41`）、本書はその**根拠（ER が audio を直受けできること）**を明文化する。
3. **ER 単体（VLA なし）では Fusion box の cross-model arbitration はほぼ空振り（pass-through）になる**。本来 Fusion が拾う食い違い reason_code（`target_mismatch` 等）は「ER と VLA など複数 model の出力差」を見るものだから（§4）。ER 単体でも有用な検査（task graph 矛盾検出・低 confidence 時の clarification）は、box 分類上は Fusion ではなく **L3 Validator / L3 Handoff の責務**である。
4. Fusion の必要性は **ER + VLA（または 2 つ目以降の model 投入）** で立ち上がる（§5）。よって ER 単体 MVP では Fusion を「定義された通過点 / 将来 seam」として残し、活性化は 2 model 目の導入時にする。これは商用再利用 box の思想と一致し、将来の physical AI 普及の下地としても妥当（§6）。

## 1. Gemini Robotics-ER の入力モダリティ（一次情報）

Google Gemini API の公式 model ページを 2026-06-23 に確認した。

| 項目 | 内容 |
|---|---|
| model | `gemini-robotics-er-1.6-preview`（現行 preview。`...-1.5-preview` は shutdown 済み） |
| Inputs | **Text, images, video, audio** |
| Output | text（空間推論では正規化 2D point / bounding box などの構造化テキスト） |
| 一次情報 | `https://ai.google.dev/gemini-api/docs/robotics-overview`（取得日 2026-06-23） |

補足:

- **audio は input 一覧に明記される**が、公式 docs の robotics 例は画像・動画中心で、音声指示を直接食わせる robotics 用途の作例は提示されていなかった。当初は「API capability としては可能だが robotics command としての実証は未確認」として扱っていた。
- **✅ 実証済み（2026-06-26・PROBE-1）**: ER（`gemini-robotics-er-1.6-preview`）に `generateContent` で text(schema)+`inline_data`(audio/wav) を直接渡し → HTTP 200・**ER が音声を直接理解**して transcript＋ordered task plan を生成（`to_robotics_plan_draft` で valid `RoboticsPlanDraft`）。→ **音声は direct ER で robotics 用途も成立**。STT を ER の上流直列に入れない方針が実測で裏付けられた。詳細・harness は [`06-unfrozen-contract-resolutions.md` §5 実測結果](06-unfrozen-contract-resolutions.md) / [`tests/live/test_er_handoff_live.py`](../../tests/live/test_er_handoff_live.py)。
- 既存 spike で text / image probe は `HTTP 200` 実測済み（`docs/dev/vla-access-and-runtime-spike.md:23-28`）。audio probe は **2026-06-26 に実施・成功**（上記）。**Hermes 経由の audio は不可**（OpenAI 互換 API server は text+image_url のみ・`input_audio` は `400`）＝音声は Hermes をバイパスして direct ER（`06` §5）。

## 2. STT の要否

**結論: STT は ER 経路で必須ではない。** ER に audio を直接渡せるため、`音声 -> STT -> 文字起こし -> ER` という直列は必須でなくなる。

ただし STT は optional として残す価値がある。

| STT を残す理由 | 説明 |
|---|---|
| 人間可読の監査ログ | 「人間が何と言ったか」を `transcript` として Langfuse trace / decision event に残せる（provenance）。ER に audio を直接渡しても、transcript があると後追い・説明がしやすい。 |
| 非 ER 経路 | Mode A/B/C の比較対象である text LLM（Claude / ChatGPT / Gemini / Grok）は音声を取らない。これらの経路では STT が必須。Mode X-ER だけが audio 直入力できる。 |
| 構成の自由度 | cost / latency / privacy の都合で、ER に audio を渡さず transcript のみ渡す構成を選べる。 |
| 実証前の保険 | §1 のとおり ER の audio 用法が robotics で未実証の間、STT 経由 transcript を確実な経路として併走させられる。 |

推奨: **ER 経路では audio 直入力を第一候補、STT は fallback / audit** とする。input bundle は両方を許容する形を維持する（既存案 `docs/mode-x-er/01-architecture-and-flow.md:118-120` が `instruction_audio_ref` と optional `transcript` の両方を持つ形で既に整合）。

## 3. data flow への含意

- 「`STT -> Input Context -> ER`」を**必須直列にしない**。Input Context Box は `instruction_audio_ref` を第一級入力として持ち、`transcript` は optional とする。
- 既存の data flow 図とフローは既に STT を optional 表記しているため、**構造変更は不要**（`docs/mode-x-er/01-architecture-and-flow.md:21` `optional [STT Adapter]` / `docs/mode-x-er/README.md:41` `optional STT / transcript`）。本書はその意図と根拠を補足する位置づけ。
- Input Context Box の責務（音声 / 画像 / state / calibration を束ねる）と「実行許可を持たない」性質は productization 側の正本に従う（`docs/productization/06-oss-reuse-and-box-small-designs.md:85-100`）。

## 4. ER 単体での Fusion の要否

Fusion box は「ER / VLA / STT / WMS input が**食い違ったとき**の arbitration」を行う箱である（`docs/productization/06-oss-reuse-and-box-small-designs.md:132`）。その reason_code は **複数 model 出力の差**を分類する（`target_mismatch` / `action_mismatch` / `confidence_gap` / `unsafe_vla_action` / `needs_operator`、`docs/productization/06-oss-reuse-and-box-small-designs.md:141`）。

したがって **ER 単体（VLA なし）では cross-model disagreement が原理的に発生しない → Fusion はほぼ pass-through** になる。

一方、ER 単体でも必要な検査はあるが、box 分類上は **Fusion ではなく L3 の責務**として置く:

| 検査 | ER 単体での要否 | 置き場所（box） | 根拠 |
|---|---|---|---|
| task graph の矛盾 / cycle / 依存検出（NetworkX） | 必要 | **L3 Validator / L3 Handoff** | `docs/productization/05-decision-observability-and-tooling.md:160`・`docs/productization/06-oss-reuse-and-box-small-designs.md:148` |
| 低 confidence -> operator clarification / reject | 必要 | **L3 Validator** | ER output の `operator_clarification_required` / `detections[].confidence`（`docs/mode-x-er/01-architecture-and-flow.md:140-147`）、Validator の confidence 検査（`docs/mode-x-er/01-architecture-and-flow.md:41`） |
| 危険 / 低レベル出力の遮断 | 必要 | **L3 Handoff / Governance** | `forbidden_endpoint` / `low_level_action_present`（`docs/productization/06-oss-reuse-and-box-small-designs.md:152`） |

> つまり「`action_mismatch` の手順矛盾検出（NetworkX）は ER 単体でも使える」という指摘は正しい。**技術（DAG / cycle / 依存検査）は再利用できるが、ER 単体経路ではそれを L3 に置く。** Fusion はあくまで cross-model 専用の箱として保つ（食い違いの相手がいないと意味を持たない）。

## 5. ER + VLA での Fusion

Fusion が本領を発揮するのは ER + VLA、特に Nav2 だけでは表現できない局所操作（把持 / 配置 / ドッキング / 近接位置合わせ）が入るときである（`docs/mode-x-er-vla/04-openvla-use-cases-and-control-flow.md:15-22`）。

- ER は「過去に俯瞰画像から立てた計画」、VLA は「掴む瞬間に手元カメラで見ている現実」。両者は食い違いうる。Fusion はその差を、物理動作の前に reason_code へ畳む（`docs/mode-x-er-vla/01-integration-architecture.md:17-48` Option A）。
- ここで full arbitration（target / action / confidence / unsafe / operator）が立ち上がる。合格条件は L4F-G0〜G2（`docs/productization/06-oss-reuse-and-box-small-designs.md:143`）。

## 6. box として残す商用価値と将来の位置づけ

- ER 単体 MVP では Fusion を「定義された box（通過点）」として置き、**活性化は 2 つ目以降の model（VLA / 別 ER provider / WMS cross-check）投入時**にする。これにより、後から model を足しても architecture を組み直さずに済む。
- これは商用再利用 box の思想と一致する。box は「現時点の ROS package 境界と完全一致するとは限らない**商用再利用の保管単位**」であり（`docs/productization/01-commercial-box-map.md:5`）、再利用 box は単一 mode のものではなく LLM Bridge / Open-RMF / ER / VLA / 下位 Nav2・ESP32 を横断する（`docs/productization/README.md:30`）。
- 「食い違いを説明可能な reason_code に畳む audit seam」は、provider / model を差し替えても顧客への説明が変わらない資産になる（`docs/mode-x-er-vla/01-integration-architecture.md:42` の disagreement -> operator clarification 思想と整合）。
- **将来展望（vision・本リポジトリの凍結契約ではない）**: 中小企業まで physical AI 搭載機器が部品組み上げ式で普及する局面では、「fallible な model 群の周りに deterministic な安全 gate と説明可能な reason_code を置く」パターンが下地になりうる。本プロジェクトの L0〜L4 + box 分割は、その下地の小規模実証として位置づけられる。ただしこれは方向性であり、ここで新しい契約 / しきい値を凍結するものではない。

## 未凍結事項 / TODO

- ~~ER への audio 直入力の robotics 用途実証~~ **✅ 完了（2026-06-26・PROBE-1）**: HTTP 200・音声理解＋plan 生成を実測（`06` §5）。STT は fallback / out-of-band（provenance）として残すが、ER 経路の必須上流ではない。
- Fusion の activation 条件（例: 参照 model 数 ≥ 2 で有効化、ER 単体では pass-through）を実装時に config 化するか、box を物理的に省くかの判断。
- 低 confidence のしきい値・operator clarification policy は現場依存で、既存 tool には含まれない自作領域（`docs/productization/05-decision-observability-and-tooling.md:163`）。

## 参照

- 一次情報: `https://ai.google.dev/gemini-api/docs/robotics-overview`（Gemini Robotics-ER 1.6、Inputs = Text/images/video/audio。取得日 2026-06-23）
- `https://ai.google.dev/gemini-api/docs/models`（model 一覧。1.5 preview shutdown / 1.6 preview 現行）
- `docs/mode-x-er/01-architecture-and-flow.md`（data flow・input bundle 内部案）
- `docs/mode-x-er/README.md`（標準フロー・optional STT 表記）
- `docs/mode-x-er-vla/01-integration-architecture.md` / `docs/mode-x-er-vla/04-openvla-use-cases-and-control-flow.md`（ER+VLA・Fusion の本領）
- `docs/productization/06-oss-reuse-and-box-small-designs.md`（Fusion / L3 Handoff / Input Context box 設計）
- `docs/dev/vla-access-and-runtime-spike.md`（Gemini direct probe 実測）
