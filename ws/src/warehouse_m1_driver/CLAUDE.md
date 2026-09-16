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
- **シリアル層は #550 で裁定変更のうえ結線済**: `FUNC_MOTION=0x12` フレーム組立（`HEAD=0xFF, DEVICE_ID=0xFC, LEN, FUNC, payload…, CHECKSUM=(sum+257-0xFC)&0xFF`）は**自作せず vendor `Rosmaster_Lib` へ委譲**（下記 `backend.py` seam と同一裁定）。udev symlink `/dev/myserial` と CH340（`1a86:7523`）ドライバは **導入済 2026-09-09**（L4T に `ch341` 不在 → out-of-tree ビルド＋brltty 除去＝[docs/jetson/02-remote-access-and-dev-link.md](../../../docs/jetson/02-remote-access-and-dev-link.md) §10）。`# TODO(Phase 1)` として残るのは **115200 8N1 の open 検証（`Rosmaster_Lib` / `m1_probe`）と MCU auto-report 40ms** の実機セットアップ（`02-hardware-design.md` 残課題 5 / 10）。
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
- ✅ U-5 版一致（2026-09-10 `m1_probe`）: 実機 FW **3.6** ＝ 公式 source **V3.6.5** 系と major.minor で一致（vendor lib は patch を公開しない。旧 V3.5.1 GitHub mirror は履歴確認のみ＝`docs/shared/02-hardware-design.md:584`）。判定に使う値は次行。
- **実機 flash の `car_type` は 2026-09-10 に `0x0A`（`CAR_MECANUM_M1`）へ書換え済**（工場出荷は `0x02`＝X3 PLUS だった）。**FW version は 3.6**（vendor lib は major.minor のみ公開）＝上記 U-5 の版一致確認はこの値で行う。**ROS param `car_type` は既定 `-1`（送らない）のまま運用する**——非負値を渡すと `RosmasterBackend.__init__` が起動のたびに flash 書換えを繰り返す（通常パーサでは MCU リセット無し・即時反映。永続する値を毎回書き直すだけで意味が無い）。記録と契約 pin への含意は [`docs/adr/0010-raise-speed-cap-to-platform-max.md`](../../../docs/adr/0010-raise-speed-cap-to-platform-max.md) の **2026-09-10 追補**、実機手順は [`docs/shared/02-hardware-design.md`](../../../docs/shared/02-hardware-design.md) **P-8-3**。**電源断（USB 抜き→メインスイッチ OFF 5 s→ON→USB 挿し）をまたぐ保持は 2026-09-10 17:37 に確認済**（MCU リセット後の読み戻し 10・FW 3.6・電池 12.3 V）。

## 【2026-09-08 追記】stop overlay（停止上乗せ）core スライス（doc05 §8 順序 5 の先行 core）

設計正本: [docs/mode-m1/05-operation-state-and-stop-authority.md](../../../docs/mode-m1/05-operation-state-and-stop-authority.md) §4（停止上乗せの状態表・R-26 ①〜⑧）/ §3-2（鮮度原則: 不明・stale → fail-closed）。**既定は機能無効**（doc05:68 — standalone M0-M2 bring-up = [mode-m1/03:50](../../../docs/mode-m1/03-joystick-teleop-bringup.md) を壊さない）。「safe-OFF」とは呼ばない（doc05:59）。**W-3（ホスト死）の代替ではない**。

### 提供 (produce) — 本スライスで追加

- `M1DriverCore(backend, cmd_timeout_s=..., stop_overlay_enabled=False)` — 新 kwarg（既定 False・省略時は従来と bit 等価）。
- `M1DriverCore.on_stop_state(stop_requested: bool, valid_until: float, now: float)` — 停止上乗せの状態 feed（ROS 配線が呼ぶ seam。契約は doc05 §4-1・decode は `stop_state.py`）。`valid_until` は注入 monotonic 時計上の絶対期限。**非有限・非正・逆行する期限 → 無許可扱い**（doc05 §4 ②）。無効時は no-op。
- `M1DriverCore.stop_overlay_enabled`（read-only property）。
- ROS param **`stop_overlay_enabled`**（`driver_node.py`・既定 `False`）→ core へ注入。当時は producer 購読を配線せず（下記 TODO）→ **2026-09-09 追記スライスで配線済**。
- **新しいトピック / 型 / JSON スキーマは産まない**（doc03 契約のまま・`warehouse_interfaces` 無変更）。

### テスト（R-26）

- `tests/unit/test_m1_stop_overlay.py`（18 関数 / 25 ケース）— fake backend + fake clock・doc05 §4 由来の spec オラクル。①停止要求→ゼロ ②無効期限（非有限/非正/逆行/過去）→無許可 ③初期=停止側 ④**無効時 bit 等価**（pre-existing-API core との backend call 列完全一致 = negative oracle）⑤有効時も clamp 必経 ⑥W-1 と AND 合成（別ノブ）⑦W-2 不干渉 ⑧壁時計不使用（source pin）。
- **mutation 4 本全 KILLED**（2026-09-08 実測・commit 後に driver_core.py 実ファイル差替え+try/finally 復元方式。PYTHONPATH 影方式は conftest が勝つため不可 = 上記 2026-08-06 の教訓）: M1 停止要求無視（ハーネス自己チェック）/ M2 既定を有効へ反転 / M3 stale 分岐素通し / M4 有効時 clamp 迂回。

### 前提・未確定 (TODO)

- ~~`# TODO(doc05 OQ-OP1/OP2)` producer channel の ROS 配線は未実装（意図的 defer）~~ → **解消（下記 2026-09-09 追記の producer 配線スライス）**。契約は doc05 §4-1 が正本・doc03「Jetson 内部」表に 1 行 additive 済。
- `# TODO(Phase 1)` 統合 bringup での有効化（launch 設定の切り分け: 単体=無効 / 統合=明示有効。doc05:68）。

## 【2026-09-09 追記】stop overlay の producer 配線スライス（doc05 §4-1 契約 + 消費側）

設計正本: [docs/mode-m1/05-operation-state-and-stop-authority.md](../../../docs/mode-m1/05-operation-state-and-stop-authority.md) **§4-1（停止上乗せの入力契約）**。カタログ行は doc03「Jetson 内部」表の `/bot{n}/stop_state`（topic 名・型・一行責務のみ・詳細は §4-1 へ委譲）。**`warehouse_interfaces` 無変更**（`std_msgs/String` JSON・doc16 §3 の Phase 4 まで JSON 運用）。

### 提供 (produce) — 本スライスで追加

- `warehouse_m1_driver.stop_state.STOP_STATE_TOPIC_TEMPLATE` = `"/{bot}/stop_state"`。
- `warehouse_m1_driver.stop_state.DEFAULT_STOP_STATE_MAX_VALIDITY_S` = `0.5`（**暫定**・凍結 twist_mux 入力 timeout と整合。`# TODO(Phase 1 実測)`）。
- `warehouse_m1_driver.stop_state.decode_stop_state(payload, now, max_validity_s) -> (stop_requested, valid_until)`
  — **純関数・rclpy 非依存**。JSON パース不能・非 object・キー欠落・型不一致・非有限/非正/逆行する `valid_until` は**すべて即時失権**（`(True, nan)` を返し core 規則②に載せる＝watermark を汚さない）。未知キーは無視（additive-first）。受理時は `min(valid_until, now + max_validity_s)` に**上限クリップ**。
- ROS param **`stop_state_max_validity_s`**（`driver_node.py`）— **W-1 の `cmd_vel_timeout_s` とは別 param**（doc05:69）。非有限・非正は既定へ fail-safe。
- `driver_node` の **`/{bot}/stop_state` 購読**（`std_msgs/String`・QoS **RELIABLE / KEEP_LAST depth 1 / VOLATILE**）。**`stop_overlay_enabled: true` のときだけ購読を作る**＝既定無効では **topic トポロジも**従来どおり（command path だけでなく購読も増えない）。※ ただし `declare_parameter("stop_state_max_validity_s", …)` は無効時も走るので **parameter interface は変わる**（`ros2 param list` に 1 個増える）＝「bit 等価」は command path と topic グラフについての主張であり、param 面には及ばない。
- **新しい型 / JSON スキーマは `warehouse_interfaces` に産まない**（doc03 カタログへ 1 行 additive のみ）。

### 消費 (consume) — 追加分

- `std_msgs/String`（`package.xml` exec_depend 追加済）/ `rclpy.qos`（QoSProfile・3 policy enum）。
- topic 契約 `/bot{n}/stop_state` の **producer は Emergency Guardian（L1・`warehouse_safety` 所有）**。本スライスは consumer 側のみ＝producer publisher は後続スライス。

### テスト（R-26）

- `tests/unit/test_m1_stop_state_contract.py`（**63 ケース**）— doc05 §4-1 由来の**独立オラクル**（期待値はテスト側リテラル。実装定数を import して比較する tautology にしない）。decode の全異常系（パース不能・非 object・キー欠落/型不一致・**キー重複**・非有限/非正・**表現不能な巨大整数**・**深いネスト**）／上限クリップ／watermark 逆行・同値・rejected が watermark を上げないこと／既定無効の bit 等価（call log 完全一致）／clamp 必経・W-1 AND・W-2 不干渉／配線層 AST pin。
- **失権は「戻り値の形」と「core 越しの振る舞い」の両方で pin する**（`assert_revokes`）。振る舞いだけだと、小さい正の期限を返す変異が core の watermark に偶然弾かれて生き残る（実測で 2 体 SURVIVED → 両建てで解消）。
- CI に rclpy は無いため配線層は **AST で読む**（`test_emergency_mirror_wiring.py` / `test_speed_band_bringup_wiring.py` 先例）。**配線層は 1 行も実行されない**ので pin は**厳密一致**（topic 式・型・QoS 4 値・guard の極性・callback の 3 文）にしてある。部分一致にすると `on_stop_state(False, …)`（全停止要求が無視される fail-open）や core 呼び出しの削除が**全緑のまま通る**（レビューで実証済み）。
- **mutation 16/16 KILLED**（2026-09-09・commit 後・worktree の隔離コピーに実ファイル差替え+try/finally 復元。M1 は「必ず死ぬ」自己チェック）: 期限/窓/型の decode 変異 4・QoS/guard 変異 4・callback 変異 4（**stop_requested ハードコード**・core 未呼び出し・topic 誤り・窓 param 無視・引数入替）・decode 例外漏れ 2・キー重複 1。

### 前提・未確定 (TODO)

- `# TODO(producer)` Guardian 側 publisher は `warehouse_safety` 所有の後続スライス。**それまで `stop_overlay_enabled:=true` は feed 不在＝常時停止側**（fail-closed・設計どおり・起動 log に表示）。
- `# TODO(Phase 1 実測)` `stop_state_max_validity_s` の運用値（現在は twist_mux 0.5s に暫定整合）。
- `# TODO(doc05 §4-1)` producer 再起動で `valid_until` が watermark を下回り続ける場合の復帰手段は producer スライスで裁定（現状は fail-closed で保持）。
- `# TODO(doc05 §4-1)` **`stop_state_max_validity_s` にハード上限は無い**。退化値（非有限・非正）は既定へ落とすが、**大きな有限値は信頼して通す**＝上乗せが実質無効化されうる。W-1 `cmd_vel_timeout_s` と同じ信頼クラス（既存 idiom）だが、上限を設けるかはオペレーター裁定（doc05 §4-1「本節が裁定しないこと」）。suite では `test_a_large_finite_window_is_honoured_as_a_trusted_operator_setting` が可視化のみ行う。
- `# TODO` **ROS param の動的更新に未対応**: `stop_overlay_enabled`（#601 由来）も `stop_state_max_validity_s`（本スライス）も construct 時にしか読まない。`ros2 param set` は成功するが購読も窓も変わらない（「有効にしたつもり」が成立する）。`read_only` descriptor か `add_on_set_parameters_callback` の追加は後続。
- **同一ホスト・同一 boot の単調時計共有は §4-1 が置く前提**（doc03 から導かれたものではない）。クロスホスト化・`use_sim_time` 下では期限規律が成立せず恒久 fail-closed になる。sim での扱いは producer スライスで裁定。

## 【2026-09-13 追記】車輪スケール（L0'）＋ エンコーダ odom スライス

設計正本: **mode-outdoor/07 §9 案 A / A'（[`docs/mode-outdoor/07-drivetrain-and-wheel-sizing.md:175`](../../../docs/mode-outdoor/07-drivetrain-and-wheel-sizing.md)・裁定 = 150 mm・追補③ param 表 [`:252`](../../../docs/mode-outdoor/07-drivetrain-and-wheel-sizing.md)〜`:255`・PR #674 で main に land）** ＝ 平輪 144 mm 級（k=1.8）/ 150 mm（k=1.875）への換装。ホスト側 param 表の正本は [`docs/mode-m1/02-m1-driver-and-watchdog.md:125`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)〜`:128`（追補②）。ファーム側は stock 80 mm 幾何のまま（[`docs/shared/02-hardware-design.md:749`](../../../docs/shared/02-hardware-design.md) `CAR_M1_MAX_SPEED=700` / 周長 251.327mm＝直径80mm / `ENCODER_CIRCLE_205=2464` counts per **車輪1回転**）で**ホストから変更不可**（[`docs/mode-m1/02-m1-driver-and-watchdog.md:29`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)-33）。よって **実速度 = wire 値 × (D / 80mm)**、ホストは **`wire = actual / k`（k = D / 0.080）** を送る。odom は [`docs/mode-m1/02-m1-driver-and-watchdog.md:49`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)（⑥ `0x0D` 生カウント差分・M1 実測幾何）に従い自前で組む。

> **凍結契約は不変**: `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`（実単位 0.3 m/s）は本スライスで変えない。clamp は**実単位のまま最初に**通り、スケール除算は**その後**（wire 化の直前）。

### 提供 (produce) — 本スライスで追加

- `M1DriverCore(..., wheel_scale=1.0, yaw_scale=1.0, lateral_enabled=True)` — 既定は**今日と bit 等価**（`x / 1.0` は IEEE-754 で厳密）。
  - dispatch 順: `config_error → brake` / `lateral 無効 → vy=0`（**clamp の前**）/ `overlay → brake` / **`clamp_body_velocity`（実単位・L0' 絞り点）** / `_to_wire`（`vx/k, vy/k, wz/(k·yaw_scale)`）→ backend。
  - **fail-closed な config 検証**: `wheel_scale ∈ [1.0, 2.5]`・`yaw_scale ∈ [0.2, 5.0]`・有限のみ。外れたら `config_error` に理由を保持し、**全 command・全 watchdog tick が `stop_brake()`**（`set_body_velocity` は一切呼ばれない）。**既定値へ fallback しない**のが肝: 150mm 装着で k=1.0 に落ちると指令の 1.875 倍で走りながら clamp は 0.3 m/s と表示する＝fail-open。上限 2.5 の根拠は mode-outdoor/07 §3 (d)（前後輪干渉で実用径 ≈160〜170mm）。wire を小さく保つことは vendor lib の `int16(v*1000)` 溢れ（`struct.error` → bare except で**フレームが黙って消える**＝[`docs/adr/0010-raise-speed-cap-to-platform-max.md:15`](../../../docs/adr/0010-raise-speed-cap-to-platform-max.md)）の予防でもある。
  - read-only property: `config_error` / `wheel_scale` / `yaw_scale` / `lateral_enabled`。
- `warehouse_m1_driver.odom_core`（**新規・純 stdlib・ROS 型なし**）
  - `WheelOdometry(counts_per_rev, wheel_diameter_m, track_m, wheel_signs=(1,1,1,1))` / `update(counts, t) -> OdomSample | None`。
  - 幾何は ctor で検証（非有限・非正は `ValueError`）。`wheel_signs` は **+1/-1 の 4 要素のみ**（`0` は片輪を黙って落とすので拒否）。
  - **int32 wrap 対応**（差分を 2^32 で畳む）。初回・`dt<=0`・非有限 `t`・壊れた報告は **None で drop**（counts は消費しない＝距離を失わない）。観測経路ゆえ「捏造するより出さない」。
  - `left = mean(m1,m2)` / `right = mean(m3,m4)` / `ds=(l+r)/2` / `dθ=(r-l)/track`、姿勢は**中点方位**で積分、`vx=ds/dt` / `wz=dθ/dt`。
  - 定数 `FW_ENCODER_COUNTS_PER_WHEEL_REV=2464.0` / `FW_ASSUMED_WHEEL_DIAMETER_M=0.080`（ファームが信じている径＝k の分母。**積分には使わない**）。
  - `diagonal_covariance(in_plane, yaw_like) -> list[float]`（36 要素・対角のみ）＋ `UNOBSERVED_AXIS_COV=1e6`。配線層でなく**純モジュールに置いた**のは、対角 index（0/7/14/21/28/35）の取り違えが EKF を恒久的に誤誘導する典型バグで、host unit で殺せるようにするため。
- `backend.MotionBackend.read_encoders() -> tuple[int,int,int,int] | None` ＋ `RosmasterBackend.read_encoders()`（`get_motor_encoder()` を包み、**例外は None へ縮退**。timer 内の例外死は W-1 ごとドライバを落とし、ファームは最後の setpoint を保持したまま走る＝最悪）。
  - **モータ順**（ファーム `Motion_Set_Speed(L1, L2, R1, R2)`）: `m1`=前左 / `m2`=後左 / `m3`=前右 / `m4`=後右。
- ROS param（`driver_node.py`・すべて construct 時読み）: `wheel_scale`(1.0) / `yaw_scale`(1.0) / `lateral_enabled`(True) / `odom_enabled`(**False**) / `wheel_diameter_m`(`FW_ASSUMED_WHEEL_DIAMETER_M`=0.080) / `track_m`(0.194 **暫定**) / `counts_per_rev`(2464.0) / `wheel_signs`([1,1,1,1] **暫定**) / `odom_period_s`(0.04＝ファーム 25Hz) / `odom_twist_cov`(0.02 **暫定**) / `odom_pose_cov`(1e3 **暫定**)。
- topic **`/bot{n}/odom`**（`nav_msgs/Odometry`・[`docs/architecture/03-software-architecture.md:77`](../../../docs/architecture/03-software-architecture.md) の既存契約行。**新規契約は産まない**）。`header.frame_id = bot{n}/odom` / `child_frame_id = bot{n}/base_link` は凍結名を `warehouse_description.robot_dimensions`（`robot_dimensions.py:19,27`）から import（ローカル文字列リテラル禁止）。
  - **TF は一切出さない**（`odom→base_link` は ekf_node 単独所有＝[`docs/architecture/23-perception-and-localization.md:163`](../../../docs/architecture/23-perception-and-localization.md) / [`docs/mode-m1/02-m1-driver-and-watchdog.md:54`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)）。`TransformBroadcaster` は AST unit で禁止済。
  - **`odom_enabled: false` が既定** ＝ standalone bring-up（[`docs/mode-m1/03-joystick-teleop-bringup.md:50`](../../../docs/mode-m1/03-joystick-teleop-bringup.md)）の ROS グラフは publisher も timer も増えない（`stop_overlay_enabled` と同じ idiom。ただし `ros2 param list` には増える＝param 面は「不変」ではない）。

### 消費 (consume) — 追加分

- `nav_msgs/Odometry`・`warehouse_description.robot_dimensions`（`BASE_FRAME` / `ODOM_FRAME` のみ。`package.xml` に exec_depend 追加済＝許可された 2 共有パッケージの一方）。
- consumer は EKF `odom0=/bot1/odom(wheel)`（[`docs/architecture/23-perception-and-localization.md:154`](../../../docs/architecture/23-perception-and-localization.md)）で、**位置でなく速度 (vx, vy) を採る**（[`docs/architecture/23-perception-and-localization.md:183`](../../../docs/architecture/23-perception-and-localization.md)）。

### テスト（R-26）

| ファイル | 件数 | 役割 |
|---|---|---|
| `tests/unit/test_m1_wheel_scale.py` | 61 | 実速度 `hypot(wire)×k ≤ MAX_LINEAR_VELOCITY`・方向保存・既定 bit 等価（pre-existing API との call log 一致＝negative oracle）・不正 k/yaw の全 brake・lateral 無効・W-1/W-2 不変 |
| `tests/unit/test_m1_odom_core.py` | 49 | 1 回転＝真の周長・80mm との非一致・純旋回・中点方位・int32 wrap（2147483000→−2147483000 は **+1296 counts**）・drop 系・符号・ctor 検証・covariance 対角 |
| `tests/unit/test_m1_driver_node_odom_wiring.py` | 37 | 配線層 AST pin（CI に rclpy 無し・**1 行も実行されない**ので厳密一致）: TF 禁止（識別子走査＝散文は除外）・topic/型・param guard・凍結 frame 名・callback 文・backend は `read_encoders` のみ |

**mutation 11/11 KILLED**（2026-09-13 実測・実ファイル差替え + try/finally 復元 + `__pycache__` 毎回除去。M1 は「必ず死ぬ」ハーネス自己チェック）:

| # | 仕込んだ欠陥 | 結果 |
|---|---|---|
| M1 | k の除算を落とす（実単位をそのまま wire へ） | **KILLED** |
| M2 | `wz` が `yaw_scale` を無視（k のみで割る） | **KILLED** |
| M3 | 不正 k が停止でなく **1.0 へ fallback** | **KILLED** |
| M4 | odom がファームの 80mm 周長で積分 | **KILLED** |
| M5 | int32 wrap 未処理（素の減算） | **KILLED** |
| M6 | lateral 無効の `vy=0` を **clamp の後**に移動 | **KILLED** |
| M7 | odom publisher/timer を **無条件生成**（既定 off 崩壊） | **KILLED** |
| M8 | `child_frame_id` を凍結名でなくリテラルに | **KILLED** |
| M9 | clamp を**実単位でなく wire 値**に適用 | **KILLED** |
| M10 | watchdog が `config_error` を無視 | **KILLED** |
| M11 | covariance の yaw 対角を index 35→30 にずらす | **KILLED** |

### 前提・未確定 (TODO)

- `# TODO(実測)` **`track_m = 0.194` は実測ではなく導出値**: `MECANUM_M1_APB = 189.5 = (輪距+軸間)/2` と全幅 231.4mm からの引き算（mode-outdoor/07 §1・確度「推定」）。**全 yaw rate がこの値に反比例**する。mode-outdoor/07 §10 の G-W1（ノギス 5 分）で確定する。
- `# TODO(実測)` **`wheel_signs` は未検証**: 右側（m3/m4）が前進で負にカウントする可能性がある。既定 `[1,1,1,1]` は「仮定」であり、実機 `m1_probe` の encoder delta で確定する（[`docs/mode-m1/03-joystick-teleop-bringup.md`](../../../docs/mode-m1/03-joystick-teleop-bringup.md) §2）。符号が逆なら odom は**前進を後進**と報告する（安全機構ではないが EKF を汚す）。
- `# TODO(実測)` **`yaw_scale` の値**: ファームの X3 幾何（`ROBOT_WIDTH 169.0` / `ROBOT_LENGTH 160.11`）と M1 実寸の乖離ぶんの補正で、[`docs/mode-m1/02-m1-driver-and-watchdog.md:34`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)（帰結③）が「実測で確定」としている。既定 1.0 は**無補正**であって「正しい」ではない。
- `# TODO(実測)` **covariance 対角は docs に無い暫定値**（twist 0.02 / pose 1e3）。**観測しない軸（z/roll/pitch、および `vy`）は 1e6 の「不信」値**を置いてある: この差動積分は `vy` を推定しない（`linear.y` は publish もしない）ので、小さい covariance で 0 を主張すると EKF がそれを融合してしまう。doc23:183 が `odom0` から (vx, vy) を採ると書いている件と**整合を取るのは EKF config 側（doc23 所有トラック）**＝要調整の open question。
- `# TODO` **`use_sim_time` 非対応**: 積分の `dt` は `time.monotonic()`（command path / W-1 と同一時計）だが、message stamp は `get_clock()`。sim 時計下では両者が乖離する。stop_state の §4-1 と同じ信頼クラスの制約で、sim 対応は後続裁定。
- `# TODO` **param の動的更新に未対応**（既存の `stop_overlay_enabled` / `stop_state_max_validity_s` と同じ）。`wheel_scale` も `odom_enabled` も construct 時にしか読まない。`ros2 param set` は成功するが**何も変わらない**。
- `# TODO(契約)` **`MAX_LINEAR_VELOCITY` の意味の再 pin は本スライスの範囲外**。mode-outdoor/07 §9 案 A は「契約 `MAX_LINEAR_VELOCITY` を実単位で再 pin」と書いており、屋外 4 km/h 級の運用値は contract PR（[ADR-0010](../../../docs/adr/0010-raise-speed-cap-to-platform-max.md) 系譜）で別途裁定する。本スライスは**値を触らず**、clamp が実単位で効く構造だけを用意した。
- ~~`# TODO(docs)` 正本 doc が未 land~~ → **解消（2026-09-15）**: mode-outdoor/07 は PR #674（main `abf2280`）で land。本節冒頭の設計正本を file:line に差し替えた（§9 = `07:175`・追補③ param 表 = `07:252-255`・mode-m1/02 追補② = `02:125-128`）。
- `# TODO(設計)` **odom の 25 Hz ポーリング aliasing**: `_on_odom` は 40 ms 周期で vendor lib のキャッシュ（FW の 25 Hz 自動レポート `0x0D`）を読むが、レポートに順序番号・時刻が無いため、同一サンプルの再読（`vx=0` スパイク）や 2 レポート分の取り込み（2 倍スパイク）が周期のうなりで起き得る。`update()` は「更新なし」を区別できない。対策候補 = 高頻度（5 ms）ポーリングでカウント変化時刻をサンプル時刻にし、無変化が 2 レポート周期を超えたら零速度を publish（スライス 2）。現状は EKF の twist 共分散（0.02）で吸収させる前提＝実走で `/bot1/odom` の vx ヒストグラムを見て判断（`OQ-OD69` 後半と同じ実測ゲート）。
- `# TODO(設計)` **`yaw_scale < 1` は wire の `wz` を拡大する**（`_to_wire` の除算は x/y を縮めるが、`wz / (k·yaw_scale)` は yaw_scale=0.2 で 5 倍）。`wz` は未 clamp（既存 TODO）のため、vendor lib の `int16(v*1000)` 溢れ点が `32.767·k·yaw_scale` rad/s（k=1.875・0.2 で ≈ 12.3 rad/s）まで下がる。角速度 clamp（契約なし）の導入時に合わせて閉じる。

### 【2026-09-16 追記】運用値の注入元（consume・本パッケージの既定は不変）

- **consume（設定の受け手）**: [`ws/src/warehouse_bringup/config/m1_wheel_plain150.yaml`](../warehouse_bringup/config/m1_wheel_plain150.yaml)（**bringup 所有**）。150 mm 通常輪換装後の運用値（**5 キーちょうど** = `wheel_scale` 1.875 / `wheel_diameter_m` 0.150 / `lateral_enabled` false / `odom_enabled` true / `counts_per_rev` 2464.0）を、**明示的に渡したときだけ**注入する。**適用は 150 mm 装着と同時**（未適用のまま 150 mm で走るのが 1.875 倍の fail-open。G-W ゲートは適用条件ではなく odom の使用制限）:
  `ros2 run warehouse_m1_driver m1_driver --ros-args --params-file "$(ros2 pkg prefix warehouse_bringup)/share/warehouse_bringup/config/m1_wheel_plain150.yaml"`
- **本パッケージの既定は 1 つも変わっていない**（`driver_node.py` / `driver_core.py` 無変更）。素の `ros2 run` は stock 80 mm と bit 等価のまま＝[`docs/mode-m1/03-joystick-teleop-bringup.md:103`](../../../docs/mode-m1/03-joystick-teleop-bringup.md) の手順は据え置き。launch 自動注入もしない（driver 起動＝車輪通電のため。W-4 = [`docs/mode-m1/02-m1-driver-and-watchdog.md:65`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md)）。
- **PROVISIONAL は driver 側に残っている**: **`yaw_scale`(1.0)** / `track_m`(0.194) / `wheel_signs`([1,1,1,1]) / `odom_period_s`(0.04) / `odom_twist_cov`(0.02) / `odom_pose_cov`(1e3) は profile に**書かれていない**＝G-W1 / G-W4 / `m1_probe` の実測が入るまで本ファイルの既定が唯一の出所（二重定義を作らない）。**`yaw_scale` が特に重要**: profile 側に `1.0` を置くと、G-W4 の実測値をここ（driver 既定）へ入れた瞬間に profile が**黙って 1.0 へ上書き**する。実測値は必ず本ファイルの既定として入れ、profile には書かない。
- **型の注意（他の param ファイルを書くときも）**: `counts_per_rev` は `FW_ENCODER_COUNTS_PER_WHEEL_REV = 2464.0` で宣言されており ROS 型は **DOUBLE**。params file に `2464`（int）と書くと rclpy が `InvalidParameterTypeException` を投げて**ノードが起動しない**。`wheel_scale` / `wheel_diameter_m` も同様に float リテラルで書く。
- 設計正本 = [`docs/mode-m1/02-m1-driver-and-watchdog.md`](../../../docs/mode-m1/02-m1-driver-and-watchdog.md) 追補③（置き場所・適用条件）/ [`docs/mode-outdoor/07-drivetrain-and-wheel-sizing.md`](../../../docs/mode-outdoor/07-drivetrain-and-wheel-sizing.md) 追補④。R-26 pin = `tests/unit/test_m1_wheel_plain150_profile.py`（18 本・mutation 12/12 KILLED）。
