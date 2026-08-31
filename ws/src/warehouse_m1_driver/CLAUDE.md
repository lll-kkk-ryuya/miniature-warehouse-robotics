# warehouse_m1_driver — ROSMASTER M1 ホスト側シリアルドライバ（L0' 速度クランプ）

- **担当トラック / ブランチ**: hw/rosmaster-m1（現ブランチ `docs/rosmaster-m1-adoption` 上で先行スライス）
- **Phase**: 1（M1 実機 bring-up）
- **ビルド**: ament_python
- **編集境界**: **このパッケージ配下のみ**。`warehouse_interfaces`（凍結契約）・`firmware/`・`config/`・他パッケージは触らない（変更は `.claude/rules/parallel-workflow.md` §4 の contract PR 経由）。

## 責務

Yahboom ROSMASTER M1 の公式 STM32 source V3.6.5 は入手済みだが、stock FW の M1 clamp は 0.7m/s で、本プロジェクトの凍結契約 0.3m/s や command-stream watchdog を実装しない。custom FW fork へ置換しない現行方針では、自前 ESP32 ファーム（`firmware/include/safety_clamp.h`）と同じ MCU 内クランプ（L0）を置けない。代わりに
**ホスト側シリアルドライバの送信直前（L0'）** — body 速度を `int16(v*1000)` へ変換して `FUNC_MOTION=0x12` フレームを組む直前 — で 0.3 m/s をハードクランプする。ここが全 `/cmd_vel` が必ず通る**単一の絞り点**であり、Nav2 / Policy Gate / Emergency Guardian のいずれが壊れても wire に上限超は出ない。

- 正本: [`docs/shared/02-hardware-design.md:325`](../../../docs/shared/02-hardware-design.md)（残課題 7・方針決定 2026-08-05）
- L0' の限界: [`docs/shared/02-hardware-design.md` P-7c](../../../docs/shared/02-hardware-design.md) — ホストプロセスが生きている間だけ有効。stock FW に通信途絶停止が無いため、`# TODO(Phase 1)` G-g MCU command-stream watchdog の実装・抜線試験が必要。
- ベクトルクランプの必然性: [`docs/shared/02-hardware-design.md:371`](../../../docs/shared/02-hardware-design.md)（C-8）— 軸独立クランプでは対角 √(0.3²+0.3²)=**0.424 m/s** で上限を 41% 超過する。
- distro / 決定記録: [`docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md:50`](../../../docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md)

## 提供 (produce)

- `warehouse_m1_driver.clamp.clamp_body_velocity(vx, vy, wz) -> tuple[float, float, float]`
  — **L0' クランプ（純関数・ROS 非依存・stdlib のみ）**。
  - 3 値のいずれかが**非有限（NaN / ±inf）→ `(0.0, 0.0, 0.0)`（fail-safe stop）**。`warehouse_interfaces.safety.clamp_velocity`（`safety.py:31-32`）・`firmware/include/safety_clamp.h:38` と同一思想。
  - 線速度は**ベクトルの大きさ** `hypot(vx, vy)` でクランプし、**方向を保つ**（軸独立クランプはしない＝C-8）。
  - `wz` は**このスライスではクランプしない**（下記「前提・未確定」）。
- `warehouse_m1_driver.clamp._scale_to_magnitude(vx, vy, max_magnitude)` — 上限を引数で受ける下位関数（単体検証用）。**非有限・非正の上限は `(0.0, 0.0)` で停止**（増幅器化を防ぐ＝`clamp_velocity` 負 cap fail-open #169 / `safety_clamp.h:29` の教訓）。
- **新しいトピック / 型 / JSON スキーマは産まない**（doc03 契約のまま）。

## 消費 (consume)

- `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`（= 0.3 m/s・**単一ソース**）。`safety.py:8-12` により**値の再定義・ハードコードは禁止**。
- 他トラックの内部モジュールは import しない（`package.xml` の依存は `warehouse_interfaces` のみ。`python3-pytest` は ament boilerplate の `test_depend`）。

## テスト

- **R-26 安全 unit の対象**（独立オラクル ＋ mutation で赤くなること＝[`docs/architecture/20-dev-quality-and-testing.md`](../../../docs/architecture/20-dev-quality-and-testing.md) §9 / `.claude/rules/safety.md`）。
- 本スライスは**実装のみ**。unit は**別担当が独立オラクルで作成**する（実装者がテストを書くと oracle が impl-coupled になるため分離）。
- 固定すべき契約（テスト作成者向け）: 非有限 3 パターン→全ゼロ / 対角 (0.3, 0.3) → 合成 ≤ 0.3 かつ方向保存 / 範囲内素通し / `(0, 0)` でゼロ除算しない / 上限ちょうど / `wz` 素通し / `_scale_to_magnitude` に 0・負・NaN・inf 上限を渡して増幅しない。
- **事後条件**: 戻り値は常に `hypot(vx, vy) <= MAX_LINEAR_VELOCITY`。単純な 1 回スケーリングだけでは極端値で**丸めにより 1 ulp 上限超**になる（実測: `(1e308, 1e308)` → 0.30000000000000004。`MAX/mag` が subnormal になるため）。よって同一係数での**再スケール 1 回**（方向は厳密に保存）＋なお超える場合は `(0.0, 0.0)` へ fail-safe、を実装に入れてある。通常域では両方 no-op（ランダム 20 万件で over-cap 0 件・方向ドリフト 0 件を実測）。
- ROS 非依存・pure stdlib なので host（`.venv` py3.12）の pytest で完結する。

### 実施結果（2026-08-06・241 tests green）

```
.venv/bin/python -m pytest tests/unit/test_m1_clamp.py tests/unit/test_m1_scale_guard.py -q
```

**unit は `tests/unit/` に置く**（`ws/src/<pkg>/test/` ではない）。理由: `pyproject.toml:43` が `testpaths = ["tests"]` を指定しており、CI の素の `pytest`（`.github/workflows/ci.yml:31`）は **`ws/src/**` を収集しない**。当初 `ws/src/warehouse_m1_driver/test/` に置いたところ CI から不可視で、**安全テストが偽 GREEN になる**ところだった。repo 内の既存 unit 124 本も全て `tests/unit/` にある。import はルート `conftest.py` が `ws/src/<pkg>/` を `sys.path` に足すため素の `from warehouse_m1_driver.clamp import ...` で通る。marker は他の R-26 suite と同じ `@pytest.mark.safety` + `@pytest.mark.unit`（module 冒頭の `pytestmark`）。

**2 suite 構成。混ぜないこと**:

| ファイル | 種別 | 役割 |
|---|---|---|
| `tests/unit/test_m1_clamp.py`（22 関数 / 218 ケース） | **黒箱・独立オラクル** | 実装を読まない担当が仕様のみから作成。公開関数 `clamp_body_velocity` だけを対象とし、**私有関数を import しない**（すると oracle が impl-coupled になり R-26 の担保が壊れる） |
| `tests/unit/test_m1_scale_guard.py`（23 ケース） | **白箱・防御契約** | `_scale_to_magnitude` の「不正な上限で増幅しない」契約。公開 API は常に定数 `MAX_LINEAR_VELOCITY` を渡すため、この分岐は黒箱からは**原理的に到達不能** |

**mutation 結果（R-26 の実証）** — 実装に欠陥を仕込み、suite が赤くなるかを確認:

| # | 仕込んだ欠陥 | 結果 |
|---|---|---|
| M1 | ベクトル大きさ → **軸独立クランプ**にすり替え（本命） | **KILLED** |
| M2 | 境界 `<=` → `<` | **SURVIVED = 等価変異**（下記） |
| M3 | 非有限入力を stop でなく**上限にスナップ** | **KILLED** |
| M4 | 非正・非有限の上限ガードを削除（#169 の増幅器化） | **KILLED**（`test_m1_scale_guard.py` が捕捉） |
| M5 | スケールを `vx` のみに適用＝**方向が保存されない** | **KILLED** |
| M6 | スケール係数を `wz` にも掛ける | **KILLED** |
| M7 | `math.hypot` → `math.sqrt(vx**2 + vy**2)` | **KILLED**（`1e200**2` が `OverflowError`＝安全層が例外死してクランプが効かなくなる事故） |

> **M2 は「テストの穴」ではなく等価変異**。`magnitude == max_magnitude` のとき `<` 側は `scale = max/mag = 1.0` を掛けるだけで、`vx * 1.0 == vx` ゆえ出力がビット単位で一致する。上限ちょうどの 8 ベクトル（軸上4・3-4-5 の 3 象限・任意角）で両分岐の出力完全一致を実測して確認済み。**観測可能な差が無いのでどのテストでも殺せず、殺そうとすべきでもない。**
>
> M4 は当初 **SURVIVED だった**（黒箱 218 本が全緑のまま通過）。私有ヘルパの防御分岐に公開 API から到達できないためで、これを塞ぐために `test_m1_scale_guard.py` を追加した経緯を残す。
>
> **mutation ハーネスの落とし穴（再現時の注意）**: パッケージを別ツリーへ複製して `PYTHONPATH` で差し込む方式は**機能しない**。ルート `conftest.py` が `sys.path.insert(0, ws/src/<pkg>)` で先頭に入れるため常に実物が勝ち、**全変異体が「生存」して偽の安心を返す**（実際に一度そうなった）。`clamp.py` 自体を一時的に差し替えて `try/finally` で復元すること。加えて **「必ず死ぬはずの変異体（M1）が死ぬこと」をハーネス自身の自己チェックとして先に走らせる** — これが無いと壊れたハーネスの出力を信じてしまう。

## 前提・未確定 (TODO)

- `# TODO(contract)` **角速度 `wz` の上限が未定義**。凍結契約 `warehouse_interfaces.safety` に角速度定数が無く、`.claude/rules/docs-first.md` は docs に無いしきい値の発明を禁じている。既存の唯一の数値は `firmware/include/config.h:10` `MAX_ANGULAR_VELOCITY = 2.0f`（それ自体が「Phase 1 実測」placeholder ＋ ESP32 build flag スコープ）。→ **実測後に contract PR で `warehouse_interfaces.safety` へ昇格させるか要決定**（`.claude/rules/parallel-workflow.md` §4）。昇格したら `clamp_body_velocity` で `wz` もクランプする。
- `# TODO(hand-off)` **配置の暫定性**: `02-hardware-design.md:371`（C-8）は「(vx, vy) の大きさでクランプする関数」を **`warehouse_interfaces` 側に追加**する想定で書かれている（contract PR 対象）。本スライスは L0'（残課題 7）に従い**ドライバ package-local に実装**した。共有化が必要になった時点で `warehouse_interfaces.safety` へ移管する（`.claude/rules/implementation-and-dependencies.md` §5・移管時は本 package-local 版を削除して二重定義を残さない）。
- `# TODO(Phase 1)` **G-g MCU command-stream watchdog** を追加し、host test と実機 USB 抜線試験で停止を確認する。stock V3.6.5 に通信途絶停止が無いことは source で確定済み。Emergency Guardian の明示 stop は host/USB 断では送れないため代替にならない（`02-hardware-design.md` P-7c）。
- **シリアル層は #550 で裁定変更のうえ結線済**: `FUNC_MOTION=0x12` フレーム組立（`HEAD=0xFF, DEVICE_ID=0xFC, LEN, FUNC, payload…, CHECKSUM=(sum+257-0xFC)&0xFF`）は**自作せず vendor `Rosmaster_Lib` へ委譲**（下記 `backend.py` seam と同一裁定）。`# TODO(Phase 1)` として残るのは udev symlink `/dev/myserial`・CH340（`1a86:7523`）・115200 8N1・MCU auto-report 40ms の実機セットアップ（`02-hardware-design.md` 残課題 5 / 10）。
- **`linear.y` は既定 0.0**（メカナム逆運動学は STM32 側にあり、diff-drive 契約のまま成立＝`02-hardware-design.md:364`）。omni 化は任意の後続拡張。

> **注**: 上記 `docs/shared/02-hardware-design.md:NNN` の行ピンは、`docs/rosmaster-m1-adoption` の **commit `172d339`** の実体で確認済（325=残課題7 / 329=L0' の限界 / 364=linear.y=0 で成立 / 371=C-8）。同 doc は現在も改訂中のため、行がずれたら再ピンすること。

## 設計ドキュメント

- [`docs/shared/02-hardware-design.md`](../../../docs/shared/02-hardware-design.md) — M1 採用・L0' 決定・C-1〜C-8 書き換え表
- [`docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md`](../../../docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md) — distro（Humble）決定
- [`docs/architecture/12-infrastructure-common.md`](../../../docs/architecture/12-infrastructure-common.md) — Layer マップ（L0 の定義。M1 採用に伴う改訂が `# TODO(採用時)` として残っている）
- `.claude/rules/safety.md` / `.claude/rules/docs-first.md` / `.claude/rules/parallel-workflow.md`

## 【2026-08-26 追記】serial driver node スライス（G-l 実体化・console_scripts 解消）

設計正本: [docs/mode-m1/02-m1-driver-and-watchdog.md](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)（W-1〜W-4 の多層停止・G-g 手順）/ [docs/mode-m1/03](../../../docs/mode-m1/03-joystick-teleop-bringup.md)（M0/M1/M2 ゲート・プローブ）。

### 提供 (produce) — 本スライスで追加

- console_script **`m1_driver`**（`driver_node.py`）— `/{bot}/cmd_vel`（`geometry_msgs/Twist`・doc03:88）を購読し、**`clamp_body_velocity` 必経（L0'）** → backend（vendor `Rosmaster_Lib`）へ dispatch。**publish なし・TF なし**（odom は幾何実測後の後続スライス。`odom→base_link` は ekf 単一所有＝doc23:163）。
  - **W-1**: `cmd_vel_timeout_s`（ROS param・既定 `DEFAULT_CMD_TIMEOUT_S=0.5`＝twist_mux.yaml:44 と整合）超過で毎 tick brake。非有限/非正の param は既定へ fail-safe。
  - **W-2**: atexit + SIGINT/SIGTERM + finally で `stop_brake()`（=`set_car_motion(0,0,0)`）→ `reset_state()`（=`FUNC_RESET_STATE 0x0F`）の**二重停止・冪等**。
- console_script **`m1_probe`**（`probe.py`）— **read-only** 実機プローブ（car_type / version / battery / encoder×4。**motion 送信なし**）。
- `backend.MotionBackend`（Protocol）+ `RosmasterBackend` — serial 実装の注入 seam（doc16 §11 fake seam。**フレーミングは自作しない**＝wire プロトコルは docs の凍結範囲外・vendor lib に委譲）。
- `driver_core.M1DriverCore` — rclpy 非依存の中核（unit の対象）。

### 消費 (consume) — 追加分

- `Rosmaster_Lib`（vendor・robot イメージのみ・**lazy import**。dev host の unit は import 不要）
- rclpy / geometry_msgs（package.xml exec_depend 追加済）

### テスト（R-26）

- `tests/unit/test_m1_driver_core.py`（16 関数）— fake backend + fake clock・**spec 由来の独立オラクル**（clamp 内部を読まない）。dispatch cap / 方向保存 / 非有限→ゼロフレーム / W-1 境界・repeat / W-2 順序・冪等。
- **mutation 4 本全 KILLED**（2026-08-26 実測・個別適用）: ①clamp bypass→6 fail ②軸独立クランプ→5 fail ③W-1 disarm→7 fail ④W-2 片肺→2 fail。クリーン 0 fail。

### 前提・未確定 (TODO)

- `# TODO(Phase 1)` `cmd_vel_timeout_s` の運用値は実機で確定（doc mode-m1/02 §3 W-1）。
- `# TODO(Phase 1)` odom スライス（`get_motor_encoder` 差分 + M1 実測幾何。X3 幾何のファーム報告は使わない＝mode-m1/02 §1-3）。
- `# TODO(Phase 1)` 実機ファーム版と調査ソース V3.5.1 の一致確認（U-5・`m1_probe`）。
