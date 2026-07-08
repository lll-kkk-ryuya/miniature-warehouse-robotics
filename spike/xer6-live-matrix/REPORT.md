# XER6 live matrix REPORT — ER in Hermes → x_er_bridge backbone 一気通貫（2026-07-08）

[worktree: mwr-xer6-live-matrix | branch: feat/mode-x-er-live-matrix | track: #342]

## 結論（TL;DR）

**live Gemini Robotics-ER（標準 8644 fork Hermes gateway）→ handoff → plugin composition gate →
L3（Validator/Resolver/TaskGraph/Compiler）→ frozen Command → MCP/Policy Gate → dispatch 記帳
→ goal_result completion → cycle 2（赤→青順序）が一本の線で閉じた。** doc07 §5-1 の
「live で Validator まで通す一本線 = XER6 の仕事」を、**dispatch 記帳（0 actuation）まで**実測で通した。

- **12/12 live sends（承認バッチちょうど・超過ゼロ・全 send Hermes 経由・direct fallback ゼロ）**
- 5 種の run manifest バリアントで **plugin composition が live で機能**（実 hookimpl 3 種・
  namespaced reason code・emergency clamp・site 差替）
- per-box 計測: **ER call が cycle 時間の >99.9%**（median 4.68s）、L3 チェーン全体は sub-ms
- 副産物の実測知見 2 件（§5: pixel 非決定性 / Policy Gate 鮮度 × ER レイテンシ）

## 1. 実行サマリ（3 live バッチ・operator 承認 ≤12 sends・doc07 §4.5）

| batch | 内容 | sends | 結果 |
|---|---|---|---|
| 20260708-153643 | 5 バリアント × 2 reps（cycle2=replay） | 10 | 10/10 PASS（下記 §4/§5-1 の通り dispatch は fail-closed 0 件） |
| 20260708-154840 | B_in ×1 + `--pixel-hints` | 1 | 非空 Command 到達・**Policy Gate `robot_unavailable` reject**（§5-2 の発見） |
| 20260708-155104 | B_in ×1 + `--pixel-hints` + FreshState | 1 | **完全一気通貫 GREEN**（下記） |

最終 probe の実測（audit.jsonl・work/B_in_rep1）:
```
cycle1: bot1 → shelf_1 dispatched (gen_id=1, status=ok) → mark_running(t1) → goal_result(succeeded) → mark_succeeded(t1)
cycle2: bot2 → shelf_2 dispatched (gen_id=2, status=ok)   ← after t1.completed の順序を L3 が構造的に保持
```
全 dispatch は `nav2_forwarder=None` の記帳形（forwarding フィールド無し）= **0 actuation**。

## 2. per-box 計測（pooled・全 live バッチ）

| box | median | 備考 |
|---|---|---|
| **er_propose**（live ER via Hermes 8644） | **4.679s**（min 2.603 / max 6.185・n=12） | cycle 時間の >99.9% |
| er_send | ≈ er_propose（request 組立 ~0.3ms） | 全 12 send transport=hermes・fallback 0 |
| composition_startup（build_x_er_runtime） | ~5.4ms | manifest+plugin+site gate+witness 込み |
| handoff_draft | ~0.16ms | |
| plugin_gate（validate_with_plugins） | ~0.13-0.6ms | plugin 3 種込みでも sub-ms |
| l3_compile（compile_raw_output 全体） | ~0.4ms | `--l3-substages` で内訳取得可（offline 検証済） |
| gen_mint / align_task_ids / dispatch / mark_running / completion_apply | 0.01-1.1ms | |

**tokens**: prompt 838（hint 付き 900）/ completion median 321（min 183 / max 326）/
**バッチ総計 13,627 tokens**（12 calls）。Hermes 経由でも OpenAI 互換 `usage` は**取得できた**（欠落なし）。

**baseline 比較（参考・非等価条件）**: 2026-06-27 実測は Hermes 3.69s / direct 4.24s（n=4・別 payload・
`deploy/hermes/er-audio-fork/README.md:76-77`）。本ラウンド median 4.68s は同オーダー。n≤12 のため
median/min/max のみ主張（p95 は主張しない）。

## 3. manifest × plugin composition の live 検証（本ラウンドの主目的）

| バリアント | manifest | live で観測された挙動 |
|---|---|---|
| A zero-plugin | variant_a.yaml | preflight vacuous pass・core validator のみ・**accepted** |
| B_in zone 全域 | variant_b.yaml | `l3.zone_policy` 登録・pass・（最終 probe で dispatch まで） |
| B_out zone 非交差 | variant_b.yaml | **決定的 reject**: `l3.zone_policy:target_out_of_zone` ×2 発火・composed status=rejected・0 dispatch・store 無傷（R-26） |
| C 2 plugin + customer_b | variant_c.yaml | `l3.confidence_policy` の **warning 帰属**（非ブロック）×全 cycle・witness に 2 plugin 登録・site=customer_b |
| D emergency clamp | variant_d.yaml | `l3.escalation_probe:keepout_breach` 発火・**`clamped_from=emergency_stop`**（allowlist 外要求を BLOCK に降格）・status=rejected（emergency_stop に**ならない**） |

- witness（`out/<batch>/runs/<variant>/<run_id>/effective_composition.json`）が各バリアントの
  registered_plugin_ids / site_profile を記録 = 「同一コードで manifest だけ差し替え → 構成が変わる」を実証。
- 既知の witness 盲点: B_in / B_out は同一 manifest（zone polygon は constructor 注入）のため witness は同一。
  polygon 値を witness に載せるなら plugin profile artifact 化が筋（doc09/doc10 の site profile 側）。

## 4. R-26 / 安全不変条件（live で全保持）

- 非 accept（B_out/D plugin reject・batch1 の resolver 不成立）は**すべて 0 dispatch**・store 無接触
- 全 dispatch destination ∈ 凍結 `KNOWN_LOCATIONS`・action=navigate のみ
- `WAREHOUSE_LIVE_ER=1` は sanctioned runner 内のみ・sender 層台帳で 12 send ハード cap
  （敵対レビュー 3 面: cost-guard 2 findings 修正済 / rules 0 findings / correctness 1 finding 修正済）

## 5. 実測知見（このラウンドが買った学び）

1. **live pixel 非決定性**: 画像無し text-only の live ER はモデルが pixel を発明し、Visual Resolver の
   snap（0.25m）に**まず解決しない** → accepted なのに empty Command（fail-closed が正しく作動）。
   実運用は camera 由来 detections が前提。ハーネスは `--pixel-hints`（ground-truth pixel を指示に併記）で
   full-chain を閉じた。
2. **Policy Gate 鮮度 × ER レイテンシ**（`policy_gate.py:34-35`）: `UNAVAILABLE_AFTER_S=2.0` に対し
   live ER は 4-6s。**ER 呼び出し前に取った state snapshot は dispatch 時点で必ず失効**し
   `robot_unavailable` reject（batch2 で実測）。本番は `warehouse_state` の 10Hz State Cache（doc12）が
   並行更新するため問題にならないが、**G5 sim 統合では State Cache 稼働が dispatch 成立の前提条件**になる
   ことを実測で固定した。ハーネスは `FreshStateToolExecutor`（dispatch 直前更新 = 10Hz writer の代役）で再現。

## 6. Honest limits（このラウンドが証明していないこと）

- **RUNNING ではない**: rclpy `XErBridge` node の spin・Gazebo 2-bot actuation（G5 human gate・#342 DoD）は未達。
  本ラウンドは node と同一の backbone 関数列を harness が駆動した（`test_x_er_offline_e2e` と同形）。
- Langfuse live trace 着地（#88）は対象外（tracer 既定 OFF のまま）。
- 音声 modality（input_audio）は未使用（text leg のみ。8644 fork は audio native 対応済だが本ラウンド外）。
- n=12 のため latency は median/min/max のみ。p95 が要るなら n≥20 の別バッチ（新 cost gate）。
- cycle2 は envelope replay（`--cycle2-live` は未使用 = live 2nd call の非決定性は未計測）。

## 7. Follow-up 候補（自動実行しない）

1. **G5 sim gate**（#342 主線）: `mode_x_er.enabled:true + forward_to_nav2:true` + 2-bot Gazebo（dev/08）。
   本ラウンドの知見 2 件（pixel 供給・State Cache 稼働）はそのまま前提条件リストに入る。
2. harness の land PR（このブランチ）＋ dev/07 §4.5 への live-matrix 手順追記。
3. Langfuse leg（#88）: `LangfuseTranscriptTracer` enabled 化の live 検証。
4. image 添付 ER call（overhead_image_ref + BlobLoader）で pixel-hints 無しの自然 resolve を検証。

## 再現手順

```bash
# 無課金
spike/xer6-live-matrix/run-live-matrix.sh --offline
# 課金（operator cost 承認後・cap≤12、MWR_LIVE_BUDGET で縮小のみ可）
deploy/hermes/er-audio-fork/run-er-gateway.sh          # gateway 起動（別端末 or bg）
spike/xer6-live-matrix/run-live-matrix.sh --pixel-hints --variants B_in --reps 1
deploy/hermes/er-audio-fork/run-er-gateway.sh --stop   # teardown
```

生データ: `out/20260708-{153643,154840,155104}-live/`（gitignore・results.jsonl / summary.json / witnesses / audit.jsonl）
