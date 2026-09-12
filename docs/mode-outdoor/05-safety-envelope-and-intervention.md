# 05 — 安全包絡と介入（非常停止装置・リンク断・横断ゲート・fail-active）

作成日: 2026-09-12
Status: **記載済（既存契約への写像・実装未）**。§2 の流用表と §3 の契約写像は 2026-09-12 のエージェントチーム（アーキ・安全レーン）の報告を統合し、repo の pin は執筆時に実 Read（[D]/[L]/[I] の凡例は [03](03-localization-gnss-and-ekf.md) と同じ）。**新規 producer の topic・型・閾値はすべて additive 提案（未凍結）**。

> 正本ルート: [mode-outdoor/README](README.md)。法規は [01](01-legal-envelope-japan.md)、既存停止権限の正本は [mode-m1/05](../mode-m1/05-operation-state-and-stop-authority.md)、cmd_vel トポロジは [architecture/12 §cmd_vel](../architecture/12-infrastructure-common.md:526)。図解は [outdoor-localization-perception-flow.html](outdoor-localization-perception-flow.html) §4-5。
> レイヤ注記: 新規 producer 3 点 + 物理非常停止 latch = **L1 Safety**、横断ゲート = **L2 Governance**、減速（帯）= **best-effort 制御面（安全機構ではない＝[ADR-0012](../adr/0012-speed-band-no-l2-best-effort.md)）**、物理非常停止装置 = **L0（電源遮断）**。

## 0. 位置づけ（最小安全方針との関係）

最小安全方針（[GLOSSARY §11](../GLOSSARY.md)）は「自機保護の最小線」を hard 床として残し、それ以外は攻める。屋外は**法が要求する 3 点**（§1）が hard 床に加わる。本 doc はその 3 点を既存契約に**写像**し、**新しい許可判断ノードを作らない**（[mode-m1/05:9](../mode-m1/05-operation-state-and-stop-authority.md:9) の裁定＝第 2 の許可判断点を新設しない）という規律の下で新規 producer を置く。

## 1. 法が要求する 3 点（設計要件への写し・正本は [01 §8 / 追補②](01-legal-envelope-japan.md)）

| 要件 | 設計要件 | 置き場 |
|---|---|---|
| 遠隔の人が常に操作できる（前進・後退・停止・加減速・右左折、**見通し外から**） | PC 卓の手動 takeover（停止専用では不足）+ 映像 | [02 §2-1](02-architecture-split-orin-pc-cloud.md) |
| 通信断で自律継続しない | リンク断 → 停止・再アームまで停止のまま | §3 |
| 物理非常停止装置（φ20 mm 以上・赤/黄・地上 60 cm 以上・前後 2 か所または上部中央 1 か所・直ちに原動機停止・解除まで再開しない） | モータ電源を切るリレー + GPIO latch + 遠隔通知 | §5 |

## 2. 既存停止資産の流用

| 要件 | 既存で流用できるもの | 新規に要るもの |
|---|---|---|
| 手動 takeover | `teleop_joy` の deadman（[mode-m1/03:49](../mode-m1/03-joystick-teleop-bringup.md:49)）・`/joy` 鮮度 0.6 s（[:51](../mode-m1/03-joystick-teleop-bringup.md:51)）・非常停止 latch と再アーム（[:52](../mode-m1/03-joystick-teleop-bringup.md:52)・正本 [mode-m1/05 §5-6](../mode-m1/05-operation-state-and-stop-authority.md:113)） | `operator_link_node`（[02 §2-1](02-architecture-split-orin-pc-cloud.md)）が WS 入力を `/joy` 相当に再生。純ロジックは流用し入力源だけ差し替え |
| 遠隔非常停止 | `/operator/stop_request` の engage / clear（§3-1） | PC 卓のボタンを同じ契約に載せる |
| 通信断で停止 | 再アーム条件（解除・3 軸中立・deadman 押し直し＝[mode-m1/05:132](../mode-m1/05-operation-state-and-stop-authority.md:132)） | **リンク断 watchdog**（§3） |
| 物理非常停止 | なし（拡張ボードのメインスイッチ・T プラグ抜きは手が届く距離＝[01 §3](01-legal-envelope-japan.md) の要件を満たさない） | §5 |
| fail-active 対策 | W-1 / W-2（ホスト内・[mode-m1/02 §3](../mode-m1/02-m1-driver-and-watchdog.md:64)） | W-3 = [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)。屋外では W-4（手を掛けたまま）は成立しないため、**物理非常停止 + W-3 が W-4 の代替**（§6） |

## 3. 新規 producer と既存契約への写像（すべて L1 Safety・R-26 unit 必須）

### 3-1. 既存契約（実ファイルで確認）

| 項目 | `/operator/stop_request`（操作者非常停止要求） | `/bot{n}/stop_state`（停止上乗せ・L0' 向け） |
|---|---|---|
| 型 / payload | `std_msgs/String` JSON `{"action": "engage"\|"clear"}`（[mode-m1/05:163](../mode-m1/05-operation-state-and-stop-authority.md:163) OQ-OP2 裁定）[D] | `std_msgs/String` JSON `{"stop_requested": bool, "valid_until": float}`（[mode-m1/05:83](../mode-m1/05-operation-state-and-stop-authority.md:83) / [stop_state.py:4-5](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/stop_state.py:4)）[D] |
| QoS | RELIABLE / KEEP_LAST(10)（[warehouse_safety/CLAUDE.md:23](../../ws/src/warehouse_safety/CLAUDE.md:23)）[D] | RELIABLE / KEEP_LAST / depth 1 / VOLATILE（`transient_local` は fail-open ゆえ不使用＝[CLAUDE.md:16](../../ws/src/warehouse_safety/CLAUDE.md:16)）[D] |
| producer / consumer | `teleop_joy` → Emergency Guardian（[emergency_guardian.py:190](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:190)）[D] | Guardian（[emergency_guardian.py:171](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:171) / [:290](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:290)）→ `m1_driver`（[stop_state.py:30](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/stop_state.py:30)）[D] |
| fail の向き | **不明・不正 payload は無視**（停止を掛ける方向の入力なので無視が安全側＝[mode-m1/05:98](../mode-m1/05-operation-state-and-stop-authority.md:98)）[D] | **パース不能・キー欠落・型不一致は即時失権**（走行を許す方向の入力だから fail-closed）[D] |
| latch | `OperatorStopLatch`（engage で ON・**明示 clear のみで OFF**＝[guard_logic.py:255-256](../../ws/src/warehouse_safety/warehouse_safety/guard_logic.py:255) / [CLAUDE.md:33](../../ws/src/warehouse_safety/CLAUDE.md:33)）[D] | 毎 50 ms tick・`valid_until = now + 0.5 s` |
| 停止の実体 | latch 中は毎 50 ms `/bot{n}/cmd_vel/emergency`（prio100 = FROZEN・[12:529-540](../architecture/12-infrastructure-common.md:529)）へゼロ Twist を level 再アサート | L0' が送信直前で停止上乗せ |
| 再アーム | 解除 ≠ 走行開始。スティック中立観測 → deadman 押し直し（[mode-m1/05:116](../mode-m1/05-operation-state-and-stop-authority.md:116) / [:132](../mode-m1/05-operation-state-and-stop-authority.md:132)）[D] | — |

### 3-2. 写像表（提案・未凍結）

核心の制約: 「**新しい許可判断ノードは作らない**」（[mode-m1/05:9](../mode-m1/05-operation-state-and-stop-authority.md:9)）・「速度経路に新ノードは足さない」。→ 新規 producer は**すべて Guardian の「停止理由の集合」へ合流**させる（[mode-m1/05:115](../mode-m1/05-operation-state-and-stop-authority.md:115) の実装 idiom: `BotState` へ既定値付きフィールド追加 + `evaluate` の追加ブロック + 純ロジック側 latch）。

| 新規要素 | 分類 | 契約上の形 | 注意 |
|---|---|---|---|
| **リンク断 watchdog** | stop_request producer（L1） | Orin 内。PC heartbeat の途絶（閾値は [02 §3-3](02-architecture-split-orin-pc-cloud.md)・単一ソース＝`OQ-OD25`）で engage。`reason_code = link_loss`。**解除は再アーム条件経由のみ**（リンク復帰で自動 clear しない） | 届出事項「通信断絶時の制御方法」の実体 |
| **GNSS 品質ゲート** | stop_request producer（停止）+ 帯セレクタ入力（減速） | **段分け**: `heading 未収束 = 発進禁止`／`float = 減速（best-effort）`／`single・非有限・ロスト = 停止`（[03 §4-3, §5](03-localization-gnss-and-ekf.md)） | **減速は安全機構として数えない**（§3-3） |
| **ジオフェンス** | stop_request producer（L1） | 歩道ポリゴン外・車道進入で engage。`reason_code = geofence_exit`。**横断状態のときだけ横断歩道ポリゴンを許可** | 許可の拡大は restrict-only（[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md)）と向きが逆＝`OQ-OD56` |
| **物理非常停止の GPIO latch** | stop_request producer（L1）+ 電源遮断（L0） | ボタン押下 → リレーでモータレグ遮断（§5）→ GPIO → `reason_code = estop_hardware` → 遠隔通知 | 電源遮断が主・ソフト latch は記録と通知 |
| **横断ゲート** | **L2 Policy Gate の restrict-only profile**（stop producer ではない） | §4 | L2 の出口は position goal のみ（L2-G8＝[ADR-0012:13](../adr/0012-speed-band-no-l2-best-effort.md:13)）＝速度経路に触れない |

**合流 topic の推奨（`OQ-OD53`）**: producer ごとに topic を作らず、`/operator/stop_request` と同型の**単一 additive topic**（例 `/safety/stop_request`・`std_msgs/String` JSON `{"reason_code": "...", "action": "engage"|"clear"}`）を 1 本足し、Guardian 単一 consumer / 単一 latch 機構に集約する。理由: consumer が 1 つで凍結トポロジに触れない・fail 方向が `/operator/stop_request` と同一・doc03 契約カタログへの追記が 1 行で済む。

### 3-3. 「減速」は安全機構ではない（既存裁定の屋外適用）

- 既存の runtime speed limiter は `speed_limit`（`nav2_msgs/SpeedLimit`・絶対 m/s）を 20 Hz publish（[speed_band_node.py:73](../../ws/src/warehouse_perception/warehouse_perception/speed_band_node.py:73)）し、controller_server が消費する [D]。
- **ADR-0012 決定 11「単一 publisher 規律」**（[0012:28](../adr/0012-speed-band-no-l2-best-effort.md:28)）: `/bot{n}/speed_limit` の publisher は 1 本のみ。→ GNSS 品質ゲートは**第 2 の publisher になれない**。帯セレクタの**入力**として合流させ `min` を取る（`OQ-OD55`）。
- **ADR-0012 Context 3**（[0012:14](../adr/0012-speed-band-no-l2-best-effort.md:14)）: Humble MPPI では帯が黙って消える経路が複数あり、帯値は厳密上界にならない。→ 「float に落ちたので減速して安全を確保する」は成立しない。**縮退で安全を担保するのは停止（stop_request）だけ**（`OQ-OD54`）。

## 4. 横断ゲート（L2）

- 状態機械 `approach → wait → cross → done`。`cross` へ遷移できるのは **「信号 = `GREEN`（[04 §4](04-perception-sidewalk-and-signals.md) の fail-closed 契約）かつ 遠隔操作者の承認トークン有効」の AND のみ**。`GREEN_FLASHING` / `RED` / `UNKNOWN` は開始禁止。
- 既存 Policy Gate の **restrict-only profile**（[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md)）で表現する: 横断 waypoint（route の `crossing_enter` tag）の dispatch を許可するか否か。L2 の出口は position goal のみ（L2-G8）＝速度経路には触れない。
- `cross` 中の承認失効・青点滅は「残距離で分岐（終える / 引き返す）」＝[01 §6](01-legal-envelope-japan.md)。「引き返す」は stop_request では表現できないため、**L3 task graph 側の abort / reverse ノード**として持つ（`OQ-OD59`）。
- LLM / ER は状況説明のみ。`UNKNOWN` を `GREEN` に昇格できない（クラウド断で fail-closed）。

## 5. 物理非常停止装置（法定・L0）

- 要件（[01 §3 / 追補②](01-legal-envelope-japan.md)）: 押しボタン φ20 mm 以上・赤 + 境界黄・**最下部の地上高 60 cm 以上**・配置は前後 2 か所または**上部中央 1 か所**（前端 / 後端からボタンまでの長さ + 車体高さ ≤ 170 cm）・押下で直ちに原動機停止・解除操作まで再開しない。
- 実装案: **バッテリー → 拡張ボード間のモータレグにリレー / 接触器**を入れ、ボタンで遮断。**Orin レグは切らない**（Orin だけ落とすと MCU が最後の速度目標を保持する fail-active＝[mode-m1/02:25](../mode-m1/02-m1-driver-and-watchdog.md:25)）。ボタン状態を Orin GPIO で読み、ソフト latch（§3-2）と遠隔通知（届出項目「非常停止装置作動時の遠隔操作者への通知方法」）へ。
- 現状の遮断手段（拡張ボードのメインスイッチ・T プラグ抜き＝[shared/02:897](../shared/02-hardware-design.md:897)）は手が届く距離での操作で押しボタン要件を満たさず、走行主スイッチ（Orin レグのみ）は非常停止にならない。
- 柱（60 cm 以上）は GNSS アンテナ・カメラと同居（[06 §5](06-hardware-delta-and-base-selection.md)）。突出 8 mm ルールを適用。

## 6. fail-active 対策

- 事実: stock FW に command timeout なし・IWDG 無効（[mode-m1/02 §1-2](../mode-m1/02-m1-driver-and-watchdog.md:25)・[shared/02:723](../shared/02-hardware-design.md:723)）。ホスト死・USB 断で MCU は最後の速度目標を保持して走り続ける。
- 屋外の代替: **物理非常停止（§5）+ W-3（[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)）**。W-3 を公道の前提条件にするかは `OQ-OD52`。ADR-0013 の前提ゲートに toolchain（配布ソースは Keil のみ）を加える申し送りは [07 §8-5](07-drivetrain-and-wheel-sizing.md)。
- vendor FW の屋外に効く挙動（**低電圧 9.6 V × 2 s のラッチ停止＝電源再投入まで復帰不能**・ゼロ指令 = 短絡ブレーキ・yaw-adjust ビット・SBUS 中立デバウンス）は [07 §8](07-drivetrain-and-wheel-sizing.md)（車体側正本は [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補）。低電圧ラッチは **operational stop** として遠隔卓へ通知する producer 候補（`OQ-OD74`）。
- 走行主スイッチ（エーモン 4962・Orin レグのみ＝[shared/01:187](../shared/01-budget-and-procurement.md:187)）を非常停止と誤認しない運用規律（[mode-m1/02 §3 W-4](../mode-m1/02-m1-driver-and-watchdog.md:65)）。拡張ボードのメインスイッチは Orin レグを遮断できず（[shared/02:913](../shared/02-hardware-design.md:913)）、T プラグ抜きが現状唯一の全遮断（[shared/02:897](../shared/02-hardware-design.md:897)）。
- **recovery（BackUp / Spin）の collision_monitor BYPASS**（#233 ⑥）は R-42 200 mm 隘路の deadlock（[12:560](../architecture/12-infrastructure-common.md:560)）＝ジオラマ固有の根拠。歩道では recovery が車道側へ動く危険があり、**公道では BYPASS を再裁定**（`OQ-OD58`）。

## 7. R-26 unit 一覧（実装時・独立オラクル・mutation で赤くなること＝[architecture/20 §9](../architecture/20-dev-quality-and-testing.md)）

| unit | 固定する性質 |
|---|---|
| リンク断 watchdog | 途絶 → engage・復帰しても自動 clear しない・閾値は単一ソース定数 |
| GNSS 品質ゲート | heading 未収束 → 発進禁止・float → 減速入力のみ（停止しない）・single / 非有限 / ロスト → 停止・自動復帰しない |
| ジオフェンス | ポリゴン外 → 停止・横断状態でのみ横断ポリゴンを許可・非有限 pose → 停止 |
| 横断ゲート | `GREEN` かつ 承認 の AND 以外で `cross` へ遷移しない・`GREEN_FLASHING` で開始しない・`UNKNOWN` は禁止 |
| 物理非常停止 latch | GPIO 押下 → 停止理由 → 通知・解除操作まで再開しない |
| Guardian pose 源 | `/amcl_pose` 不在で沈黙しない（屋外 pose 源で `pose_stale` が発火する） |
| `operator_link_node` | 運ぶ意味の閉集合以外を publish しない（AST pin・doc22 と同型） |

## 8. OPEN QUESTIONS（接頭辞 `OQ-OD5*`）

- `OQ-OD50` リンク断閾値（秒）と LTE の実測ジッタ。
- `OQ-OD51` 非常停止リレーの型（機械リレー / 半導体）・定格・自己保持回路。
- `OQ-OD52` W-3 を公道の前提にするか。
- `OQ-OD53` 新規 producer の合流 topic 形（個別 topic か単一 `/safety/stop_request` か）。
- `OQ-OD54` GNSS 劣化時の「減速」を安全機構として数えないことの明文化（float → 減速 / ロスト → 停止 の段分け）。
- `OQ-OD55` ADR-0012 決定 11（`speed_limit` 単一 publisher）との整合: 帯セレクタの入力に合流させるか、屋外では帯セレクタ自体を差し替えるか。
- `OQ-OD56` ジオフェンスの「横断時ポリゴン許可」は restrict-only 規律に反する。状態依存の許可拡大をどう安全に表現するか。
- `OQ-OD57` Guardian の pose 源交代に伴う変位ゲート（motion_epsilon / odom_freshness）の意味（AMCL の motion-gated 沈黙は GNSS には無い）。
- `OQ-OD58` recovery の collision_monitor BYPASS を公道で維持するか。
- `OQ-OD59` 横断中の承認失効・青点滅時の「引き返す」を L3 task graph の abort / reverse ノードとして持つか。
- `OQ-OD5A` 語彙 gate の代替: `KNOWN_LOCATIONS` 9 キー（[locations.py:11-23](../../ws/src/warehouse_interfaces/warehouse_interfaces/locations.py:11)・`Command` の検証＝[schemas.py:160](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py:160)）を増やさず座標 goal seam を使う想定は「語彙 gate の穴」（[STATUS.md:150](../STATUS.md:150) H-4）。到達集合の制約をジオフェンスへ移す旨を明文化するか、`locations.py` に屋外語彙を additive で足すか（contract PR）。

## References

- [01 法規包絡](01-legal-envelope-japan.md)（§3 非常停止装置 / §5 遠隔操作の解釈 / §8 設計への写像 / 追補② 型式認定基準）/ [02 §2-1, §3](02-architecture-split-orin-pc-cloud.md) / [03 §4-5](03-localization-gnss-and-ekf.md) / [04 §4](04-perception-sidewalk-and-signals.md) / [07 §8](07-drivetrain-and-wheel-sizing.md)
- [mode-m1/05-operation-state-and-stop-authority.md](../mode-m1/05-operation-state-and-stop-authority.md)（[:9](../mode-m1/05-operation-state-and-stop-authority.md:9) 許可判断点を新設しない / [:83](../mode-m1/05-operation-state-and-stop-authority.md:83) stop_state / [:98](../mode-m1/05-operation-state-and-stop-authority.md:98) fail の向き / [:113](../mode-m1/05-operation-state-and-stop-authority.md:113) §5 / [:115](../mode-m1/05-operation-state-and-stop-authority.md:115) idiom / [:116](../mode-m1/05-operation-state-and-stop-authority.md:116) latch / [:132](../mode-m1/05-operation-state-and-stop-authority.md:132) 再アーム / [:163](../mode-m1/05-operation-state-and-stop-authority.md:163) OQ-OP2）
- [mode-m1/02-m1-driver-and-watchdog.md:25](../mode-m1/02-m1-driver-and-watchdog.md:25)（fail-active）/ [:64](../mode-m1/02-m1-driver-and-watchdog.md:64)（W-3）/ [:65](../mode-m1/02-m1-driver-and-watchdog.md:65)（W-4）/ [mode-m1/03:49,51,52](../mode-m1/03-joystick-teleop-bringup.md:49)
- [warehouse_safety/CLAUDE.md:16,23,33](../../ws/src/warehouse_safety/CLAUDE.md:23) / [emergency_guardian.py:171,190,290](../../ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:190) / [guard_logic.py:255-256](../../ws/src/warehouse_safety/warehouse_safety/guard_logic.py:255) / [stop_state.py:4-5,30](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/stop_state.py:30) / [speed_band_node.py:73](../../ws/src/warehouse_perception/warehouse_perception/speed_band_node.py:73)
- [architecture/12-infrastructure-common.md:529-540](../architecture/12-infrastructure-common.md:529)（cmd_vel トポロジ）/ [:560](../architecture/12-infrastructure-common.md:560)（BYPASS 根拠）/ [ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) / [ADR-0012:13,14,28](../adr/0012-speed-band-no-l2-best-effort.md:28) / [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)
- [shared/02-hardware-design.md:723](../shared/02-hardware-design.md:723) / [:897](../shared/02-hardware-design.md:897) / [:913](../shared/02-hardware-design.md:913) / [shared/01:187](../shared/01-budget-and-procurement.md:187) / [locations.py:11-23](../../ws/src/warehouse_interfaces/warehouse_interfaces/locations.py:11) / [schemas.py:160](../../ws/src/warehouse_interfaces/warehouse_interfaces/schemas.py:160) / [STATUS.md:150](../STATUS.md:150)
- [architecture/20-dev-quality-and-testing.md](../architecture/20-dev-quality-and-testing.md)（R-26）
