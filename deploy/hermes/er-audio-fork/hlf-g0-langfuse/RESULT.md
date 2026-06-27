# HLF-G0 probe RESULT (transcription target — fill from the live run)

> **状態（2つを厳密に区別する）**:
>
> 1. **Option D / predict-seed = LIVE-OBSERVED PASS（2026-06-27・#360 spike）**. 本 package の
>    `run-er-gateway-langfuse.sh`（plugin ON）経由で audio が ER に HTTP 200 で届き、**plugin が
>    trace を決定的 seed `create_trace_id(seed="H::H")` の位置に着地**させることを #360
>    （`spike/langfuse-plugin-d/verify_d_audio.py`）が live 検証（観測 trace = `d1477eef…`）。
>    ＝「plugin-owned trace は実体として観測できる・score-join は同 seed 再導出で成立しうる」は **実証済み**。
>    pointer: PR #360（`feat/langfuse-plugin-on-d`）/ `spike/langfuse-plugin-d/README-verify-d.md`。
>    （**注意**: #6 scorer 脚まで通した end-to-end join の live 実証は #360 でも human-gate＝下記参照。）
>
> 2. **この package の literal HLF-G0 probe（下表）= 未実行（empty template）**。これは Option D とは
>    **別の問い**＝「plugin が *inbound* `trace_id` を honor するか」。静的解析の予測は **stock=FAIL**
>    （plugin は trace_id を自生成・inbound を読まない＝`PLUGIN-TRACEID-ANALYSIS.md`・`README-hlf-g0.md:46`）。
>    Option D が PASS なのは「inbound honor」ではなく「seed 一致で再導出」という escape hatch（同 doc §6 OPTION B）。
>    本表は `run-hlf-g0.sh` の live 結果を **人が転記**する先（main session が `HERMES_LANGFUSE_*` creds 投入後）。
>
> **secret 値・trace 内容は転記しない**（PASS/FAIL と短い note のみ。trace id を載せるなら #360 が公開済みの
> `d1477eef…` のように **既に PR で開示済みのもの**に限る）。

- run date:        _____________
- gateway:         lean ER Hermes gateway（run-er-gateway.sh）port _____
- langfuse spec:   `pip install --target` で供給した版 = _____________（plugin imports から pin）
- HERMES_HOME:     ___________________（隔離 home・`~/.hermes` 以外であること）

## Gate verdicts（正本 = `docs/productization/02-l4-robotics-bridge-box.md`:190-195 の HLF-G0〜G5
## ＝ `docs/architecture/13-hermes-setup.md`:561-566 §7.7.1 条件 1〜6 と 1:1 対応）

> gate ID は **doc02:190-195 を正本**とする（条件番号 N → HLF-G(N-1)）。managed-prompt link は
> doc02 の独立 gate ではなく HLF-G1 metadata / Pattern-A の edge として `README-hlf-g0.md` §managed-prompt
> caveat で扱う（本表からは外し、doc02 と番号がズレないようにした）。

| gate（doc02 正本） | 問い | verdict | note（値・trace は書かない） |
|---|---|---|---|
| **HLF-G0** trace id passthrough（doc02:190 / doc13 cond.1） | inbound `trace_id`（または同等 correlation id）を honor し同 trace に generation が載るか | ☐ PASS / ☐ FAIL | |
| HLF-G1 metadata（doc02:191 / cond.2） | `gen_id`/`run_id`/`provider`/`mode`/`env`/prompt を trace metadata/tags に載せられるか | ☐ PASS / ☐ FAIL | |
| HLF-G2 score join（doc02:192 / cond.3） | Bridge 外の Warehouse Orchestrator が同 trace に `create_score` できるか | ☐ PASS / ☐ FAIL | |
| HLF-G3 span shape（doc02:193 / cond.4） | MCP tool span と model generation が同じ trace に入るか | ☐ PASS / ☐ FAIL | |
| HLF-G4 fail-open（doc02:194 / cond.5） | plugin / Langfuse 障害時も robot 制御が 0-dispatch / fail-open を破らないか | ☐ PASS / ☐ FAIL | |
| HLF-G5 no double generation（doc02:195 / cond.6） | wrapper drop ＋ plugin ON で generation が **1 本だけ**（二重計上ゼロ） | ☐ PASS / ☐ FAIL | |

## 判定（Pattern B 採否）

- **HLF-G0 = PASS かつ HLF-G5 = PASS** → Pattern B 着手可（WRAPPER-REMOVAL-PLAN.md §8.2 の Bridge-code PR gate を解錠）。
- **HLF-G0 = FAIL** → §6.2 に分岐: (a) plugin に inbound trace_id を honor させる fork tweak → 再 probe / (b) Pattern A（wrapper）維持。
- metadata channel（trace_id を載せたキー名 = extra_body / metadata / header）: _____________
- 結論（1 行）: _________________________________________________
