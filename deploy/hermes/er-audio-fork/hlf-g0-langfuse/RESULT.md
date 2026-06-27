# HLF-G0 probe RESULT (transcription target — fill from the live run)

> **状態**: 未実行（empty template）。本ファイルは `probe-hlf-g0.sh` の live 結果を **人が転記**する先。
> live は main session が `HERMES_LANGFUSE_*` creds 投入後に逐次実行する（WRAPPER-REMOVAL-PLAN.md §7/§8）。
> **secret 値・trace 内容は転記しない**（PASS/FAIL と短い note のみ）。

- run date:        _____________
- gateway:         lean ER Hermes gateway（run-er-gateway.sh）port _____
- langfuse spec:   `pip install --target` で供給した版 = _____________（plugin imports から pin）
- HERMES_HOME:     ___________________（隔離 home・`~/.hermes` 以外であること）

## Gate verdicts（doc13:558-570 §7.7.1 / WRAPPER-REMOVAL-PLAN.md §6,§7）

| gate | 問い | verdict | note（値・trace は書かない） |
|---|---|---|---|
| **HLF-G0** | inbound `trace_id`（request metadata）→ 同 trace に generation が載るか | ☐ PASS / ☐ FAIL | |
| HLF-G2 | Bridge から同 trace に session_id/provider/mode/env tags+metadata を足せるか | ☐ PASS / ☐ FAIL | |
| HLF-G3 | MCP tool span（Bridge in-process）を同 trace の子に入れられるか | ☐ PASS / ☐ FAIL | |
| HLF-G4 | managed-prompt link が plugin 経路で維持されるか | ☐ PASS / ☐ FAIL | |
| HLF-G5 | wrapper drop ＋ plugin ON で generation が **1 本だけ**（二重計上ゼロ） | ☐ PASS / ☐ FAIL | |

## 判定（Pattern B 採否）

- **HLF-G0 = PASS かつ HLF-G5 = PASS** → Pattern B 着手可（WRAPPER-REMOVAL-PLAN.md §8.2 の Bridge-code PR gate を解錠）。
- **HLF-G0 = FAIL** → §6.2 に分岐: (a) plugin に inbound trace_id を honor させる fork tweak → 再 probe / (b) Pattern A（wrapper）維持。
- metadata channel（trace_id を載せたキー名 = extra_body / metadata / header）: _____________
- 結論（1 行）: _________________________________________________
