# 速度上限を「ミニチュア安全値 0.3 m/s」から「プラットフォーム上限」へ引き上げる

**Status**: accepted（2026-08-19 オペレーター決定「本フェーズは安全面より速度を優先し、出せる限り出す」。**最終値の pin は §Open の実機確認と S-SPEED 実測待ち**＝コードの契約値変更は本 ADR の後続 contract PR で行う）

本フェーズ（M1 単騎・部屋スケール＝[ADR-0009](0009-m1-room-scale-operation.md)）では、凍結契約 `MAX_LINEAR_VELOCITY = 0.3 m/s`（[safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py)）を**ミニチュアスケール由来の安全方針値から「プラットフォームが物理的・ファームウェア的に出せる上限」へ再定義**し、運用速度は config（`safety.max_linear_velocity` ≤ 契約上限）で段階的に引き上げる。ジェスチャ速度制御（右手の指カウント6段階・2026-08-19 ユーザー仕様）はこの運用値を最速段とする。

## Context / 背景

- **0.3 m/s の由来はミニチュアジオラマ（1.8×0.9m）前提の安全方針**（[.claude/rules/safety.md:4](../../.claude/rules/safety.md) / [12:77](../architecture/12-infrastructure-common.md)）であり、ハードウェア制約ではない。[ADR-0009](0009-m1-room-scale-operation.md) で走行環境は実際の部屋へ転換済みで、ジオラマ通路幅由来の速度事情は消えた。
- **オペレーター決定（2026-08-19）**: ジェスチャ召喚デモを主役とする本フェーズは「安全面は今回考慮しない・速度は出せる限り出す」。本 ADR はこの判断の記録であり、トレードオフ（§Trade-offs）を明示した上での採用である。
- **agent-team 調査（2026-08-19・一次情報 = 公式 `Rosmaster_Lib` V3.3.9 ソース・工場 STM32 ファーム Rosmaster V3.5.1 C ソース・520 モータ公式パラメータ表。詳細と出典は [02 §【2026-08-19 追記】](../shared/02-hardware-design.md)）で判明した事実:
  1. **ホストライブラリに速度 clamp は存在しない**。真の上限は STM32 ファーム `Mecanum_Ctrl` の**ミキシング後・各輪独立 clamp = ±1000 mm/s**（`CAR_MECANUM`=0x01。`CAR_MECANUM_MAX`=0x02 なら ±700）。M1 専用の car_type 値は存在せず、第三者の M1 実機プロジェクトは 0x01（X3 として）で駆動している。
  2. **理論最高速度は 1.13 m/s**（65mm 輪 × 1:30 / 333RPM の場合。80mm×1:30 なら 1.40、1:56 なら 0.70-0.86）。M1 の輪径・ギア比・car_type は非公開＝**実機で5分で確定できる**（§Open 1）。
  3. **ファーム clamp は方向を保存しない**（各輪独立切り捨て）。超過指令は進行方向を歪める → **L0' ホスト側ベクトルクランプ（方向保存・[clamp.py](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/clamp.py)）を新上限で維持する工学的理由**がここにある（ミニチュア安全のためではない）。
  4. ホスト側 `int16` 境界（±32.767 m/s）超は `struct.error` → **bare except でフレームが黙って消える**（停止すらしない）＝L0' が手前で絞る第2の理由。
  5. **電池電圧で最高速度は約2割落ちる**（無負荷回転数は電圧比例・12.6→9.6V で 80%）。M1 の 12V レールは**非安定化の生バッテリスルー**なので、全力加速の電圧サグは Orin ブラウンアウト（走行中の頭脳喪失）に直結する（[ADR-0005](0005-l0-battery-brownout-floor.md) と同根）。
  6. MCU 報告は **25Hz 固定**＝1.0 m/s では1周期 40mm の未観測走行（0.3 m/s 時 12mm の 3.3 倍）。odometry・L2 鮮度・L1 反応余裕のすべてに効く。

## Decision / 決定

1. **`MAX_LINEAR_VELOCITY`（凍結契約）＝プラットフォーム上限**へ意味を再定義する。値は実機 car_type のファーム clamp（**0x01 → 1.0 m/s / 0x02 → 0.7 m/s**）。pin は §Open 1 の実機確認後の **contract PR**（`contract` ラベル・[parallel-workflow §4](../../.claude/rules/parallel-workflow.md)）で行い、**本 ADR 時点でコードは 0.3 のまま**（docs 先行＝docs-first）。
2. **運用速度は config が持つ**: `safety.max_linear_velocity`（≤ 契約上限・既存の検証機構 [config.py:101-104](../../ws/src/warehouse_interfaces/warehouse_interfaces/config.py) を無改造で流用）。環境/プロファイル毎に設定し、デモの最終運用値は **S-SPEED（段階増速実測・下記）** で「制御が破綻しない最高速」を測って決める。指カウント6段階ジェスチャはこの運用値を最速段（グー）とし、段割りは config 注入（コード定数禁止＝[mode-x-er/02:98](../mode-x-er/02-l3-planning-core.md) と同規律）。
3. **L0' クランプは廃止せず維持し、`m1_driver` serial driver slice で必ず結線する**（[mode-x-er/10 §10-2②/G-l](../mode-x-er/10-room-scale-safety-review.md) のとおり現状未結線）。存在理由を「①ファーム per-wheel clamp の方向破壊からの保護 ②暴走バグ（Nav2/上位全滅時）の最終バックストップ ③int16 溢れ→フレーム黙殺の手前で必ず有限値に絞る」へ更新する。**結線と R-26 pin（dispatch 経路が `clamp_body_velocity` を必ず通る）は契約値引き上げ PR の前提条件**。
4. **ESP32 firmware（旧2台系・`firmware/`）は 0.3 のまま凍結**（[ADR-0006](0006-single-bot-first.md) の凍結資産。M1 経路と独立で、`platformio.ini` の `MAX_LINEAR_VELOCITY_MMPS=300` は変更しない）。
5. **派生する再導出を義務化する**（contract PR の DoD）:
   - L2 鮮度窓の物理論証（[policy_gate.py:43-46](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py)「0.3×2.0s=0.6m < 盤面」）は新値で再導出。**上限 >0.9 m/s では現行論証が破れる**。
   - L1 停止円の反応余裕 `margin = v_max × t_react`（[mode-x-er/10 §5-3](../mode-x-er/10-room-scale-safety-review.md)）は新 v_max でスケール → **C-3 停止ポリゴン改訂 PR と同期**。
   - `tests/unit/test_m1_clamp.py:176-185` 等の **0.3 から手計算した期待値**は cap 相対形（`0.6*CAP` 形式）へ書き換え（変更範囲の全列挙は 2026-08-19 調査＝約 90 箇所の docs 記述追従を含む）。

### S-SPEED（段階増速実測）ゲート定義

| 段 | 内容 | 記録 |
|---|---|---|
| ベンチ | 車輪浮かせで `set_car_motion` を 0.3 / 0.5 / 0.7 / 1.0 m/s スイープ | 指令 vs ファーム報告（`get_motion_data`）vs エンコーダ差分の飽和点 |
| 走行 | 直線 2m の実測時間を満充電 / 中間 / 警報直前の3電圧点で | 実効速度・スリップ率・Orin 入力電圧サグ（ブラウンアウト兆候） |
| 制御 | 各速度で V5 相当ループ復帰誤差（[23 §6](../architecture/23-perception-and-localization.md)）＋急停止距離 | AMCL/odom が破綻しない最大値 |

**合格値 = 指令追従が飽和せず・自己位置が破綻せず・Orin 電断が起きない最大速度**。これを config 運用値として採用し、結果は [02](../shared/02-hardware-design.md) へ追記する。

## 得られるもの

- 指カウント6段階の速度レンジが 0.3 m/s の頭打ちから解放され、「グー＝全力で来る」の絵が成立する。
- 上限の意味が「方針値」から「実測されたプラットフォーム真値」になり、以後の速度議論が事実基盤になる。
- 変更は単一ソース1箇所＋config で済む構造（そのために単一ソース化してあった）が実証される。

## Trade-offs / 代償（正直に）

- **人への保護余裕が縮む。** 部屋スケールでは人とロボットが同一走行平面に立ち（[ADR-0009 帰結⑦](0009-m1-room-scale-operation.md)）、protective stop の実体は L1 collision_monitor + 速度上限である（[mode-x-er/10 §10-2](../mode-x-er/10-room-scale-safety-review.md)）。上限を上げれば margin ∝ v_max で必要停止円が広がり、制動距離はさらに悪化する。**オペレーターはこれを認識した上で速度優先を決定した**（本 ADR がその記録）。緩和は C-3 の margin スケール改訂と S-SPEED の実測確認のみで、「同等の安全性」は主張しない。
- **Orin ブラウンアウトのリスクが上がる**（高速＝高電流＝サグ増・12V 非安定化スルー）。S-SPEED の電圧記録が実質のゲート。
- **odometry / AMCL の劣化**（スリップ増・25Hz 報告の未観測距離増）。自前 `m1_driver` はファーム速度報告の積分でなく**エンコーダ差分**で odom を組む（[02 §【2026-08-19 追記】](../shared/02-hardware-design.md)）。
- **docs 記述の追従コストが大きい**（0.3 の記述 約90箇所・`check_consistency.py` に速度 check が未実装で自動検出されない）。→ §Open 4。

## Considered Options / 却下

- **STM32 ファームの clamp（±1000mm/s）自体を上げる**: 不可能。Yahboom 製バイナリでコンパイル時リテラル。ホストから変更手段なし（`FUNC_RESET_FLASH` でも不変）。
- **`set_motor` 直接 PWM でファーム上限を回避**: 却下。car_type バイト無し＝逆運動学ミキサも速度 PID もバイパスし、メカナム逆運動学と odometry をホストが背負う（[02:328](../shared/02-hardware-design.md) の「過大」判断と矛盾）。同一 PWM で左右エンコーダ差 ~12% の実測報告あり＝直進しない。超低速用途の解であって高速化の解ではない。
- **契約値を今すぐ 1.0 に書き換える**: 却下（本 PR では）。car_type が 0x02（clamp 0.7）の可能性が残り、誤った上限を凍結契約に書くのは docs-first 違反。実機確認（5分）を挟んでから contract PR で pin する。
- **上限は変えず config 運用値だけ上げる**: 不可能。既存機構が `config ≤ MAX_LINEAR_VELOCITY` を強制しており（[config.py:101-104](../../ws/src/warehouse_interfaces/warehouse_interfaces/config.py)）、契約側を上げない限り 0.3 を超えられない（この fail-closed 構造自体は正しいので維持する）。

## Open / 未決

1. `# TODO(実機5分)` **car_type 問い合わせ（最優先）** / 520 モータラベルの RPM（205/333/550）/ メカナムホイール実測径（65/80mm）→ 契約 pin 値の確定（0x01→1.0 / 0x02→0.7）。
2. `# TODO(実測)` **S-SPEED 実測**（bring-up の odom/EKF 稼働後）→ config 運用値の確定。
3. `# TODO(governance)` [.claude/rules/safety.md:4](../../.claude/rules/safety.md) の「最大0.3 m/s」改訂（`.claude/**` は governance ブランチ→PR・人間承認）。
4. `# TODO(dev-tooling)` `scripts/check_consistency.py` へ `check_speed_cap` 追加（`Sources.max_linear_velocity` は読込済・未使用＝追加コストほぼゼロ）＋ docs 約90箇所の 0.3 記述 sweep。
5. `# TODO(実測)` `t_react`（scan→制動の実効レイテンシ）実測（[mode-x-er/10 §5-3/S-5](../mode-x-er/10-room-scale-safety-review.md) と同一セッション）→ C-3 停止円の margin 確定。
6. **wz（角速度）上限は今回も新設しない**（凍結契約は linear のみ＝既存方針維持。[test_m1_clamp.py:374](../../tests/unit/test_m1_clamp.py) が「wz に linear cap を適用しない」を pin 済）。

## References

- [02-hardware-design §【2026-08-19 追記】](../shared/02-hardware-design.md) — 調査確定事実の正本（ファーム clamp・モータ表・速度の出し方・音声モジュール）
- [ADR-0009](0009-m1-room-scale-operation.md)（部屋スケール運用）/ [ADR-0006](0006-single-bot-first.md)（凍結資産）/ [ADR-0005](0005-l0-battery-brownout-floor.md)（brownout floor）
- [mode-x-er/10-room-scale-safety-review.md §5-3（margin 導出）/ §10-2（L0' 未結線）](../mode-x-er/10-room-scale-safety-review.md)
- [safety.py:18](../../ws/src/warehouse_interfaces/warehouse_interfaces/safety.py) / [config.py:101-104](../../ws/src/warehouse_interfaces/warehouse_interfaces/config.py) / [policy_gate.py:43-46](../../ws/src/warehouse_mcp_server/warehouse_mcp_server/policy_gate.py) / [clamp.py](../../ws/src/warehouse_m1_driver/warehouse_m1_driver/clamp.py)
- [docs/GLOSSARY.md §11](../GLOSSARY.md)（S-SPEED の正準用語）
