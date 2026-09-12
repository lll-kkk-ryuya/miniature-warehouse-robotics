# 05 — 安全包絡と介入（非常停止装置・リンク断・横断ゲート・fail-active）

作成日: 2026-09-12
Status: **箱（skeleton）**。§2 の流用表は 2026-09-12 所見の**草案（未裁定）**。

> 正本ルート: [mode-outdoor/README](README.md)。停止権限の担当分離は [mode-m1/05](../mode-m1/05-operation-state-and-stop-authority.md)（§5 操作者非常停止要求 = [:113](../mode-m1/05-operation-state-and-stop-authority.md:113)・§6 teleop 操作条件 = [:122](../mode-m1/05-operation-state-and-stop-authority.md:122)）、watchdog 多層は [mode-m1/02 §3](../mode-m1/02-m1-driver-and-watchdog.md:64)、法規は [01](01-legal-envelope-japan.md)。

## 0. 位置づけ（最小安全方針との関係）

- 室内の[最小安全方針](../GLOSSARY.md)は「自機保護の最小線」だった。**公道の歩道は第三者がいる場所**であり、法定要件（[01 §3](01-legal-envelope-japan.md) 非常停止装置・[01 §5](01-legal-envelope-japan.md) 遠隔操作の成立・[01 §6](01-legal-envelope-japan.md) 歩行者への進路譲り）が**床**になる。多層ガバナンスの新設ではなく、既存の W-1〜W-4・stop_request・Policy Gate の体系に**法定の床を写す**。
- 私有地（閉鎖区画）フェーズは従来の最小安全方針で進めてよい（[00 §3](00-mission-and-scope.md)）。

## 1. 法が要求する 3 点（設計要件への写し・正本は [01 §8](01-legal-envelope-japan.md)）

1. **遠隔の人が常に「操作」できる**: PC 卓は停止だけでなく**手動 takeover**（前進・後退・停止・加減速・右左折）を持つ。
2. **通信が切れたら止まり、止まったままにする**（通信断で自律継続 = 自動操縦 = 枠外）。届出に「通信遅延・通信断絶時の制御方法」を書く。
3. **物理の非常停止装置で原動機を止める**（ソフト停止は代替にならない）。作動時に遠隔操作者へ通知する。

## 2. 既存停止資産の流用（草案）

| 要件 | 既存で流用できるもの | 新規に要るもの |
|---|---|---|
| 手動 takeover | `teleop_joy` の deadman（[mode-m1/03:49](../mode-m1/03-joystick-teleop-bringup.md:49)）・/joy 鮮度 0.6 s（[:51](../mode-m1/03-joystick-teleop-bringup.md:51)）・非常停止 latch と再アーム（[:52](../mode-m1/03-joystick-teleop-bringup.md:52)・正本 [mode-m1/05 §5-6](../mode-m1/05-operation-state-and-stop-authority.md:113)） | PC 側の操作を LTE 越しに Orin へ運ぶ経路（DDS を流さず WS 経由）と Orin 側の `/joy` 再生 node（L4 入力側） |
| 遠隔非常停止 | `/operator/stop_request` の engage / clear（経路 B・consumer = Emergency Guardian・L1） | PC 卓のボタンを同じ契約に載せる |
| 通信断で停止 | 再アーム条件（解除・3 軸中立・deadman 押し直し）をそのまま使う | **リンク断 watchdog**（L1 Safety の新 producer・§3） |
| 物理非常停止 | なし（拡張ボードのメインスイッチ・T プラグ抜きは手が届く距離での操作＝[01 §3](01-legal-envelope-japan.md) の押しボタン要件を満たさない） | 地上 60 cm 以上・前後 2 ボタン・赤/黄・**モータレグのリレーを切る**配線・Orin へ GPIO で latch・作動イベントの遠隔通知（§5） |
| fail-active 対策 | W-1 / W-2（ホスト内・[mode-m1/02 §3](../mode-m1/02-m1-driver-and-watchdog.md:64)） | W-3 = [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)（前提ゲート 4 点後）。屋外では W-4（手を掛けたまま）は遠隔で成立しないため、**物理非常停止 + W-3 が W-4 の代替**（§6） |

## 3. 新規 producer 3 点（何を書くか・すべて L1 Safety・R-26 unit 必須）

- `# TODO(設計)` **リンク断 watchdog**: PC → Orin の heartbeat 途絶（閾値は [02 §3](02-architecture-split-orin-pc-cloud.md) と単一ソース）で `stop_request` を engage。解除は再アーム条件経由のみ。
- `# TODO(設計)` **GNSS 品質ゲート**: fix → float → single の劣化で減速・停止（[03 §4](03-localization-gnss-and-ekf.md)）。AMCL 直購読の Guardian pose freshness guard の屋外版 pose 源。
- `# TODO(設計)` **ジオフェンス**: 歩道ポリゴン外・車道進入で停止。横断状態（§4）のときだけ横断歩道ポリゴンを許可。

## 4. 横断ゲート（L2・何を書くか）

- `# TODO(設計)` 状態機械: `approach → wait → cross → done`。**`cross` へ遷移できるのは「青 かつ 点滅なし かつ 遠隔者承認トークン有効」がすべて揃ったときだけ**（fail-closed。信号の意味は [01 §6](01-legal-envelope-japan.md) 表）。`cross` 中に青点滅 → 「速やかに終えるか引き返す」の裁定（残距離で分岐）。
- `# TODO(設計)` 承認トークンの発行元（PC 卓）・有効期限・失効条件（[02 §5 OQ-OD22](02-architecture-split-orin-pc-cloud.md)）。
- `# TODO(設計)` L3 task graph の横断ノードと L2 ゲートの契約（既存 Policy Gate の restrict-only profile＝[ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) の範囲で表現できるか）。

## 5. 物理非常停止装置（何を書くか）

- 法定・運用基準の要件は [01 §3](01-legal-envelope-japan.md)（押しボタン・前後から操作可・地上 60 cm 以上・2 か所・φ20 mm 以上・赤/黄・直ちに原動機停止・解除まで再始動しない）。
- `# TODO(設計)` 回路: バッテリー → 拡張ボード（モータ）レグに**リレー / 接触器**（[shared/02:312](../shared/02-hardware-design.md:312) の非安定化 12 V レール・[:318](../shared/02-hardware-design.md:318) 定格 4 A / ピーク 6 A の上流）。Orin レグは切らない（Orin だけ落とすと fail-active＝[mode-m1/02:25](../mode-m1/02-m1-driver-and-watchdog.md:25)）。
- `# TODO(設計)` Orin への GPIO 通知（ソフト側の latch・Guardian への stop_request engage）と遠隔操作者への通知（届出項目）。

## 6. fail-active 対策（何を書くか）

- 事実: stock FW に command timeout なし・IWDG 無効（[mode-m1/02 §1-2](../mode-m1/02-m1-driver-and-watchdog.md:25)・[shared/02:723](../shared/02-hardware-design.md:723)）。ホスト死・USB 断で MCU は最後の速度目標を保持して走り続ける。
- `# TODO(設計)` W-3（[ADR-0013](../adr/0013-stm32-command-stream-watchdog.md)）の前提ゲート通過を**公道フェーズの前提条件**にするか、物理非常停止（§5）だけで足りるとするか（裁定）。
- `# TODO(設計)` 走行主スイッチ（エーモン 4962・Orin レグのみ＝[shared/01:187](../shared/01-budget-and-procurement.md:187)）を非常停止と誤認しない運用規律（[mode-m1/02 §3 W-4](../mode-m1/02-m1-driver-and-watchdog.md:65)）。拡張ボードのメインスイッチは Orin レグを遮断できず（[shared/02:913](../shared/02-hardware-design.md:913)）、T プラグ抜きが現状唯一の全遮断（[shared/02:897](../shared/02-hardware-design.md:897)）。
- vendor FW の屋外に効く挙動（**低電圧 9.6 V × 2 s のラッチ停止＝電源再投入まで復帰不能**・ゼロ指令 = 短絡ブレーキ・yaw-adjust ビット・SBUS 中立デバウンス・再ビルド toolchain = Keil）は [07 §8](07-drivetrain-and-wheel-sizing.md)（車体側正本は [mode-m1/02](../mode-m1/02-m1-driver-and-watchdog.md) 末尾追補）。低電圧ラッチは **operational stop** として遠隔卓へ通知する producer 候補（`OQ-OD74`）。

## 7. R-26 unit 一覧（何を書くか）

- `# TODO(実装)` リンク断 watchdog（途絶 → engage・復帰しても自動 clear しない）/ GNSS 品質ゲート（劣化 → 減速・停止・非有限は停止）/ ジオフェンス（外 → 停止・横断状態の例外）/ 横断ゲート（3 条件が揃わない限り `cross` へ遷移しない・青点滅で開始しない）。独立オラクル・mutation で赤くなること（[architecture/20 §9](../architecture/20-dev-quality-and-testing.md)）。

## 8. OPEN QUESTIONS（接頭辞 `OQ-OD5*`）

- `OQ-OD50` リンク断閾値（秒）と LTE の実測ジッタ。
- `OQ-OD51` 非常停止リレーの型（機械リレー / 半導体）・定格・自己保持回路。
- `OQ-OD52` W-3 を公道の前提にするか。

## References

- [01 法規包絡](01-legal-envelope-japan.md)（§3 非常停止装置 / §5 遠隔操作の解釈 / §8 設計への写像）
- [mode-m1/02-m1-driver-and-watchdog.md:25](../mode-m1/02-m1-driver-and-watchdog.md:25)（fail-active）/ [:64](../mode-m1/02-m1-driver-and-watchdog.md:64)（W-3）/ [:65](../mode-m1/02-m1-driver-and-watchdog.md:65)（W-4）
- [mode-m1/03-joystick-teleop-bringup.md:49](../mode-m1/03-joystick-teleop-bringup.md:49)（deadman）/ [:51](../mode-m1/03-joystick-teleop-bringup.md:51)（鮮度）/ [:52](../mode-m1/03-joystick-teleop-bringup.md:52)（latch）
- [mode-m1/05-operation-state-and-stop-authority.md:113](../mode-m1/05-operation-state-and-stop-authority.md:113)（§5）/ [:122](../mode-m1/05-operation-state-and-stop-authority.md:122)（§6）
- [ADR-0013](../adr/0013-stm32-command-stream-watchdog.md) / [ADR-0004](../adr/0004-l2-restrict-only-policy-profile.md) / [shared/02-hardware-design.md:312](../shared/02-hardware-design.md:312) / [:318](../shared/02-hardware-design.md:318) / [:723](../shared/02-hardware-design.md:723)
- [architecture/20-dev-quality-and-testing.md](../architecture/20-dev-quality-and-testing.md)（R-26）
