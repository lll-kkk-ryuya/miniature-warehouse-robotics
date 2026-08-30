# ROS 2 distro を Jazzy から Humble へ切り替える（Isaac ROS on Orin ＋ ROSMASTER M1 採用に伴う）

**Status**: proposed（2026-08-05 オペレーター指示で proposed 保持＝[02:383](../shared/02-hardware-design.md)。Gazebo の扱い等 Open が閉じたら accepted へ昇格）

**Jetson Orin Nano で Isaac ROS を使う道は Isaac ROS 3.x = ROS 2 Humble しか存在しない**（最新の Isaac ROS 4.x は Jazzy だが対応プラットフォームが Jetson Thor のみ）。加えて実機候補を Yahboom ROSMASTER M1 に変更したことで、ベンダ driver 資産（深度カメラ・LiDAR・工場イメージ）も **Humble / Ubuntu 22.04 固定**であることが判明した。両者が同じ結論を指すため、distro を Humble に合わせる。代償は Gazebo の公式ペアが崩れること 1 点に集約される。

## Context / 背景

- 現行の既定は **ROS 2 Jazzy**（[03 §開発環境](../architecture/03-software-architecture.md):263 `tiryoh/ros2-desktop-vnc:jazzy` / [12 §横断](../architecture/12-infrastructure-common.md):125 / Jetson は Ubuntu 24.04）。`docs` / `ws` / `deploy` / `.github` / `.claude` に "jazzy" 記述が **363 箇所**ある。
- **決定要因①: Isaac ROS を Orin Nano で使う道は Humble しかない。** Isaac ROS 公式 Getting Started は「All Isaac ROS packages are designed and tested to be compatible with **ROS 2 Jazzy**」としつつ、対応プラットフォーム表は **Jetson Thor（T5000 / T4000）のみ**・要 **JetPack 7.1** で、**Orin Nano は載っていない**。Orin を対象に含む系列は **Isaac ROS 3.x（ROS 2 Humble ＋ JetPack 6.x ＋ 公式 dev container）** であり、これが Orin での唯一の経路。なお NVIDIA は「Orin Nano **4GB** はメモリ不足で非推奨」と注記するが、本件は **8GB** のため該当しない。
- **決定要因②**: 実機候補が ROSMASTER M1（メカナム4輪）に変わり、Yahboom 資産が Humble 固定であることを一次情報で確認した（[02 §ROSMASTER M1 採用検討時の残課題](../shared/02-hardware-design.md)）。Nuwa-HP60C 深度カメラの ROS 2 driver `ascamera` は ament_cmake ＋ ベンダ製プリビルド `.so` への静的リンクで、同梱 aarch64 バイナリの `libs/lib/aarch64-linux-gnu/readme.md` は **「5.4.1 20170404 (Linaro GCC 5.4-2017.05)」＝2017 年 GCC 5.4 ビルド**。動作報告のある distro は Foxy(20.04) / Humble(22.04) のみで、**Jazzy / Ubuntu 24.04 の成功報告が無い**。ソース非公開のため ABI が割れたら回復手段が無い。
- Jetson 側も **JetPack 6.x = Ubuntu 22.04** であり、Jazzy を使うには JetPack 7.2（エコシステム未成熟）か container 隔離が要る。Humble なら**ネイティブ**で載る。

## Decision / 決定

**ROS 2 Humble / Ubuntu 22.04 を全系（Jetson 実機・ロボット・開発コンテナ）の既定 distro とする。**

## 得られるもの

- **Isaac ROS が Orin Nano で使える**（Isaac ROS 3.x + JetPack 6.x）。Jazzy を維持する限り、Orin では Isaac ROS を選択肢に入れられない。
- **深度カメラ HP60C が実績のある distro で動く**（Jazzy 未保証問題が構造的に消える）。
- `ydlidar_ros2_driver` は `humble` ブランチが本来の対象 → **C++17 引き上げパッチが不要**（[02](../shared/02-hardware-design.md) 残課題 9 が解消）。
- **Jetson が JetPack 6.x でネイティブ**。container 不要、Yahboom 工場イメージの校正値・URDF・ekf 設定をそのまま参照できる。
- micro-ROS は Humble も公式対応（[07 §6](../shared/07-research-notes.md):22 が「Humble フォールバックは保険として残す」と記録済）。
- **Nav2 の MPPI は Humble にもリリースがある**（`nav2_mppi_controller` **1.1.20** for Humble / 1.3.12 for Jazzy）→ 現行の controller 構成（`ws/src/warehouse_bringup/config/nav2_params.yaml:115` `nav2_mppi_controller::MPPIController`）を**維持できる**。distro 変更で機能が欠落する箇所は確認した範囲では無い。

## トレードオフ / Trade-offs

- **Gazebo が唯一かつ確実な実費。** 公式ペアは **Humble ↔ Fortress / Jazzy ↔ Harmonic**。Gazebo 公式は「Harmonic can be used with ROS 2 Humble and non ROS official binary packages」としつつ、**`ros-humble-ros-gz*` と競合する**と明記しており初心者向けでないと注記する。したがって PR#43 で取得済みの環境スパイク GO（`tiryoh:jazzy` + gz-sim **8.11** ARM64 headless + `gpu_lidar`(ogre2/software GL) + `ros_gz_bridge`・[16 §10](../architecture/16-repository-and-conventions.md):215）は **そのまま引き継げない**。
  - **選択肢 A: Humble + Fortress へ落とす** — SDF / launch / bridge の書き換えに加え、**ARM64 headless で `gpu_lidar` が成立するかの再スパイクが必須**（成立可否は未知。Jazzy+Harmonic では成立が実証済というだけ）。
  - **選択肢 B: Humble + Harmonic をソースビルド** — 非公式バイナリ + `ros_gz` ソースビルド。パッケージ競合の運用リスクを恒久的に抱える。
- Nav2 が **1.1.x 系**（Jazzy は 1.3.x）。パラメータ名・既定値・挙動差の再確認が要る。
- docs の "jazzy" 記述 363 箇所の改訂。
- **EOL が近い**: Humble = 2027-05 / Jazzy = 2029-05。本プロジェクトの想定期間では実害は小さいが、記録しておく。

## Considered Options / 却下

- **Jazzy 維持 ＋ 深度カメラだけ Humble container に隔離（DDS 越し接続）**: 技術的には成立するが、カメラ 1 個のために二重 distro を恒久運用することになり、ydlidar・校正値・工場イメージといった Yahboom 資産全体が Jazzy 側に取り残される。M1 を採る限り境界が増え続ける。
- **Jazzy 維持 ＋ 深度カメラを別製品に置換**（RealSense / Orbbec の公式 Jazzy driver）: Superior 版に同梱される HP60C が無駄になり、Standard との差額の意味が消える。カメラ単体の問題としては有効な retreat plan だが、Yahboom 資産全体の distro ズレは残る。
- **Jazzy 維持 ＋ Isaac ROS 4.x（Jazzy）を使う**: 却下。Isaac ROS 4.x の対応プラットフォームは **Jetson Thor のみ**で、手持ちの Orin Nano では動かない。JetPack 7.1/7.2 へ上げても Orin がサポート表に載らない以上、解決しない。
- **Jazzy 維持 ＋ Isaac ROS を使わない**: 成立はする（本プロジェクトの認識系は Nav2 + SLAM Toolbox が主で Isaac ROS 非依存）。ただし深度カメラ・Yahboom 資産の distro ズレは残り、将来 Isaac ROS 系の知覚パッケージへ広げる道が閉じる。

## Open / 未決

- `# TODO(Phase 0.5 再スパイク)` **Gazebo をどうするか（本 ADR で唯一未決）。** 推奨は **選択肢 A（Humble + Fortress）で ARM64 headless の再スパイクを回す**。理由は選択肢 B（非公式 Harmonic ＋ `ros-humble-ros-gz*` 競合）のリスクを恒久的に抱えたくないため。**再スパイクが割れた場合の退避 = sim（dev の Mac Docker）だけ Jazzy + Harmonic に残し、Jetson 実機・ロボット・Isaac ROS は Humble とする split-distro 運用**（PR#43 の実証済み資産を捨てずに済むが、dev と prod で distro が異なる二重管理になる）。判断材料は [16 §10](../architecture/16-repository-and-conventions.md):213 の分岐点＝「Phase 0.5 が Mac 単体で完結する」前提が維持できるか。
  - **【2026-08-05 追調査】選択肢 A（Fortress）を推す根拠が強化された。**
    1. **`ros-humble-ros-gzharmonic` に arm64 バイナリは今後も提供されない。** upstream issue [gazebosim/ros_gz#614](https://github.com/gazebosim/ros_gz/issues/614)（2024-09-18 起票・arm64 バイナリ欠如の報告）は **closed as *not planned***。開発機は **Mac M4（ARM64）** なので、選択肢 B は「非公式かつソースビルド」が恒久化する。
    2. **Humble の `ros_gz` は 0.244.25 が released**（[ROS Index](https://index.ros.org/p/ros_gz/) 参照日 2026-08-05）。0.244.x は **Fortress 対応ライン**で、Humble / Ubuntu 22.04 arm64 は ROS 2 の Tier 1 プラットフォームのため **buildfarm の arm64 バイナリが期待できる**。`# TODO(再スパイク時)` 実際に `apt install ros-humble-ros-gz` が arm64 で引けるかを最初に確認する。
    3. 残るリスクは変わらず **ARM64 headless の Fortress で LiDAR センサが成立するか**（Fortress でのセンサ名が `gpu_lidar` か `gpu_ray` かを含む）。ここが通れば本 ADR を `accepted` へ上げられる。
- ~~`# TODO(関連・別決定)` **Layer 0 の速度クランプの所在**~~ → **【2026-08-05 決着・本 ADR の未決から外す】** 0.3 m/s のハードクランプは **ホスト側シリアルドライバの送信直前（L0'）** に置く。「L2 強制になる」という当初の見立ては誤りで、自前ドライバの `FUNC_MOTION` 組立直前が全 `cmd_vel` の単一絞り点になるため、Nav2 / Policy Gate より下に置ける（[02](../shared/02-hardware-design.md) 残課題 7）。distro とは独立の決定。
- docs 一括改訂（"jazzy" 363 箇所）の範囲と順序。

## 適用状況（2026-08-05 時点・**部分適用のままコミット**）

> ⚠️ **この移行は途中まで適用された状態で land させる**（オペレーター判断 2026-08-05）。どこまで進んでいるかを明示しておく。ADR 本体が `proposed` である点も変わらない。

**適用済み（13 ファイル）**: `deploy/dev/run-sim-cockpit.sh`（イメージ名 `mwr-sim:humble`）／`deploy/jetson/bin/ros-exec.sh`（`ROS_DISTRO` 既定 humble）／**`pyproject.toml`（`target-version = "py310"`＝2026-08-17 に flip 済・下記「追記（2026-08-17 その3）」）**／docs 各所（architecture 03 / 06 / 12 / 16、jetson 01、setup/jetson-deploy、shared 02 / 04 / 09、adr/README）。

**未適用（52 ファイル）**（旧 53 から `pyproject.toml` が適用済みへ移動）。特に **現時点で整合が壊れている組み合わせ**:

| 箇所 | 状態 | 症状 |
|---|---|---|
| `deploy/dev/Dockerfile` | **jazzy のまま** | `run-sim-cockpit.sh` は `mwr-sim:humble` を探すが Dockerfile は jazzy を入れる → **中身が jazzy の `humble` タグ**が焼かれる。**最優先で解消**。 |
| `deploy/dev/run-mode-a-live.sh` / `install-nav2-e2e.sh` | jazzy のまま | cockpit と distro が食い違う |
| `firmware/platformio.ini` / `firmware/spike/**` | jazzy のまま | micro-ROS 側の distro 不一致 |
| `README.md` / `AGENTS.md` / `.claude/CLAUDE.md` | jazzy のまま | 新規セッションが Jazzy 前提で判断してしまう |

`# TODO(次スライス)` 上表を解消するまで **sim cockpit（Phase 0.5）と Jetson デプロイ経路は信頼できない**。~~`pyproject.toml` の `target-version` は **py312 のまま**（一度 py310 化したが revert＝下記「追記（2026-08-17 その2）」参照。flip は PEP 695 一掃と同一 PR で行う）。~~ → **【2026-08-17 解消】`target-version = "py310"` へ flip 済**（PEP 695 一掃を同一 PR で実施＝下記「追記（2026-08-17 その3）」）。`.claude/CLAUDE.md` は governance 所有のため、人間が別 PR で更新すること（`.claude/rules/parallel-workflow.md` §7.1）。

## References

- [02-hardware-design.md §ROSMASTER M1 採用検討時の残課題](../shared/02-hardware-design.md)（HP60C の GCC 5.4 blob・ydlidar・L0 の所在）
- [03-software-architecture.md](../architecture/03-software-architecture.md):263（`tiryoh/ros2-desktop-vnc:jazzy` + Gazebo Harmonic）
- [16-repository-and-conventions.md](../architecture/16-repository-and-conventions.md):213-215（環境スパイクと GO 判定）
- [07-research-notes.md](../shared/07-research-notes.md):22（micro-ROS の Humble フォールバック）
- [nav2_mppi_controller — ROS Index](https://index.ros.org/p/nav2_mppi_controller/) — 参照日: 2026-08-05（Humble 1.1.20 / Jazzy 1.3.12）
- [Gazebo ROS Installation — gazebosim.org](https://gazebosim.org/docs/latest/ros_installation/) — 参照日: 2026-08-05（公式ペア表・Humble+Harmonic の非公式扱い）
- [Isaac ROS Getting Started — NVIDIA](https://nvidia-isaac-ros.github.io/getting_started/index.html) — 参照日: 2026-08-05（最新版=Jazzy・対応は Jetson Thor のみ・JetPack 7.1）
- [Isaac ROS Getting Started (release-3.1) — NVIDIA](https://nvidia-isaac-ros.github.io/v/release-3.1/getting_started/index.html) — 参照日: 2026-08-05（3.x 系 = Humble・Orin 対応・Orin Nano 4GB は非推奨）
- [Isaac ROS Release Notes — NVIDIA](https://nvidia-isaac-ros.github.io/releases/index.html) — 参照日: 2026-08-05

## 追記（2026-08-17）: Humble pin の新たに判明したコスト

Isaac ROS release-3.2 が **Orin + Humble 線の終点**であることを一次情報で確認した（4.x = Jazzy + Jetson Thor 専用・Orin は 4.x でサポート外）。したがって 2026-02-02 に cuVSLAM wrapper へ追加された **RGB-D 入力モード（`tracking_mode: RGBD`）には、本 ADR の選択上永久に届かない**。これは採択時に列挙されていなかったトレードオフとして記録する（**決定自体は変えない**: Orin Nano Super 8GB は 3.2 の公式サポート内＝除外は 4GB のみ、と release-3.2 System Requirements で再確認済み）。この帰結の詳細（cuVSLAM blocked-by-hardware・TARGET-1 の MOLA-LO への差し替え）は [doc23 【2026-08-17 追補】B-1](../architecture/23-perception-and-localization.md)。

- 参照: <https://nvidia-isaac-ros.github.io/repositories_and_packages/isaac_ros_visual_slam/index.html>（Updates: 2026-02-02 RGBD / Thor・Jazzy）・<https://nvidia-isaac-ros.github.io/v/release-3.2/getting_started/index.html>（3.2 = Humble・Orin・4GB のみ非推奨）— 参照日 2026-08-17

## 追記（2026-08-17 その2）: ruff target-version flip の繰延と ADR 番号の変更

- **本 ADR は 0005 → 0008 へ改番**した（main に別内容の [ADR-0005 L0 battery brownout floor](0005-l0-battery-brownout-floor.md) が先に land していた番号衝突のため。main の番号が正準）。
- ~~`pyproject.toml` の `target-version` は **py312 のまま維持**する（本ブランチが一度 py310 化したが、main に PEP 695 構文のコード（`robotics/composition/plugin_results.py` 等）が存在し repo 全体が ruff invalid-syntax になるため revert）。**py310 への flip は「PEP 695 構文の一掃 + `ruff format .` sweep」を伴う Humble 移行スライスで同一 PR として行う**（未適用 53 ファイルの Open に追加）。~~ → **【2026-08-17 その3 で解消】** 予告どおり同一 PR で flip + PEP 695 一掃を実施した（下記）。

## 追記（2026-08-17 その3）: ruff `target-version = "py310"` へ flip 済み（PEP 695 一掃と同一 PR）

「その2」で繰延した flip を実施した。`pyproject.toml` の `target-version` は **`py312` → `py310`**（`required-version = ">=0.6,<0.16"` の Ruff 版 pin は**無変更**）。

**py312 専用構文の一掃（repo 全体で 1 箇所のみ）**: `clamp_finding` の PEP 695 ジェネリック（`def clamp_finding[F: _PluginFindingBase](...)`）を module-level `F = TypeVar("F", bound=_PluginFindingBase)` へ置換した（`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/robotics/composition/plugin_results.py`）。bound・シグネチャ・戻り値の型変数は同一で、**公開挙動は不変**（変種 A/B とも clamp 後の具象クラスが保存され、天井以下の finding は同一オブジェクトを返す＝既存 unit を無改変で通過）。PEP 695 の `type` 別名・クラスジェネリックは repo 内に存在しなかった。

**副次的に判明した真の py310 非互換（構文ではなく stdlib 可用性）**: `tests/unit/test_plugins_incubator_zone_policy.py` の `import tomllib` は **3.11+ でしか stdlib に無い**。py310 では ruff の isort が third-party 扱いに変わって `I001` が出たことで発覚した（lint はドリフトの検出器として機能した）。1 箇所の利用のみだったため、モジュール収集ごと壊さないよう `pytest.importorskip("tomllib")` を当該テスト内へ移した（前例: `tests/unit/test_duckdb_join.py:25`）。**Humble/py310 ではこの 1 アサーションだけが skip される**（他は不変）。`tomli` 等の新規依存は追加していない。

**ゲート結果**: `ruff check .` = All checks passed / `ruff format --check .` = 367 files already formatted（**flip による format ドリフトは発生せず**、`ruff format .` の一括 sweep は不要だった）/ `pytest` = **2269 passed, 17 skipped**。

**残（隠さない）**: ① `target-version` は **lint の対象構文を py310 に合わせるだけ**で、開発機の実行系は依然 Python 3.12（`.venv`）＝**実 py310 での実行検証ではない**。Humble コンテナ上での実走は未適用 52 ファイル側（`deploy/dev/Dockerfile` 等）の解消後。② `requires-python = ">=3.10"` は元から py310 を許容しており本 PR で変更なし。③ 上記 `tomllib` 以外に stdlib 可用性ベースの py310 非互換が残っていないかは、ruff が構文しか見ない以上 **実 py310 実行でしか確定できない**（現時点で既知のものは無い）。→【2026-08-30 追記: ③が実機で的中（StrEnum / typing.Self / datetime.UTC）→ compat shim + AST 床ガード unit で解消】

## 追記（2026-08-28）: NVIDIA 公式 Quick Start の「JetPack 7.2 ISO インストール」は**採用不可**

実機（Orin Nano Super・**L4T R36.4.4 / Ubuntu 22.04.5 / JetPack 6.2 系**）を起動し、SSD への移行手順を
一次情報で確認する過程で判明した**罠**を記録する。

**現行の [Orin Nano Developer Kit Quick Start](https://docs.nvidia.com/jetson/orin-nano-devkit/user-guide/latest/quick_start.html)（参照日 2026-08-28）は、
x86 ホスト／SDK Manager 不要の導入経路として「Jetson ISO installation method（**JetPack 7.2 以降**で利用可能）」を案内している。**
Mac しか持たない本プロジェクトにとって一見“正解”に見えるが、**採用してはならない**:

- JetPack 7 系は **Ubuntu 24.04** ＝ 本 ADR の決定（**Humble / Ubuntu 22.04 を全系の既定**・`:16`）と正面から矛盾する。
- Orin で Isaac ROS を使う唯一の経路は **Isaac ROS 3.x（Humble ＋ JetPack 6.x）** であり（`:10`）、JetPack 7 へ上げると
  **Orin はサポート表から外れる**（4.x は Jetson Thor 専用）。決定要因①を自ら壊す。
- Yahboom driver 資産（HP60C の閉ソース `.so` 等）も **Humble / 22.04 固定**（`:5`）。

**帰結**: Mac 単独環境での SSD 移行は、公式 ISO 方式ではなく **JetPack 6 のまま実機上で rootfs をクローンする**方式を採る
（手順と実測は [jetson/02-remote-access-and-dev-link.md](../jetson/02-remote-access-and-dev-link.md)）。
**公式ドキュメントが新しい JetPack 世代を前提に書き換わっている**点に注意し、
バージョンを確認せずに公式手順をなぞらないこと。

## 追記（2026-08-30）: SSD の当面の役割 = データディスク（rootfs 移行は任意作業へ降格）

上記クローン方式は**実施するときの方式**として維持するが、実施自体を急がない判断に更新した。
2026-08-30 の board provisioning は **NVMe を `/ssd` データディスク**（GPT+ext4・`nofail`・
docker data-root / repo clone / 地図・録画置き場）として決着し（B案）、**rootfs は microSD のまま**。
理由 = rootfs 移行は boot 構成を触る＝失敗時に物理アクセス必須で、常時通電・遠隔運用
（[jetson/02 §9.1](../jetson/02-remote-access-and-dev-link.md)）と衝突する。重い I/O は `/ssd` に
載ったため移行の便益も縮小した。決着の記録と provisioning 手順は
[jetson/02 §9.6](../jetson/02-remote-access-and-dev-link.md)。Humble 本体は同日 apt ネイティブ導入済み
（実機 Ubuntu 22.04.5 で本 ADR の決定どおり）。

## 追記（2026-08-30）: 残③の的中 — 実 py3.10（Jetson 実機）で StrEnum / typing.Self / datetime.UTC 非互換が顕在化（#563）

「その3」の残③（stdlib 可用性ベースの py310 非互換は実 py310 実行でしか確定できない）が Jetson 実機
（Ubuntu 22.04.5 / Python 3.10.12）で的中した。`colcon build` は 16 pkg 全緑（ament_python は import を
実行しないため検出不能）だが、`enum.StrEnum`（py3.11+）を import する **11 ファイル**
（warehouse_interfaces 1 + warehouse_llm_bridge 10）と `typing.Self`（py3.11+・
`visual_resolver/models.py` の runtime import＝`from __future__ import annotations` でも死ぬ）が
ImportError となり、契約ハブごと import 不能だった。

**解消（contract PR / #563）**: `warehouse_interfaces/compat.py` に**単一共有 `StrEnum`**
（py3.11+ = stdlib re-export / py3.10 = `class StrEnum(str, Enum): __str__ = str.__str__`。
CPython 3.10–3.13 実測で観測同等・`__format__` 追加は不要・`auto()` は禁止＝shim では "1" に化ける）
を新設し、11 ファイルを compat 経由へ sweep。`typing.Self` は `typing_extensions.Self`
（pydantic v2 の推移的依存＝全実行環境に既存。`warehouse_llm_bridge/setup.py` に `>=4` を明示宣言）へ置換。
str() 意味論依存 3 箇所（`conversation_events` の verdict 再パース / `x_er_cycle`・`pipeline` の
0-dispatch reasoning f-string）は独立オラクル unit（`tests/unit/test_py310_compat.py`）で固定し、
`from enum import StrEnum` の再流入・`auto()` 混入は同 unit の source-scan で恒久ブロックする。

**残（隠さない）**: ① CI は py3.12 のみ（`.github/workflows/ci.yml` の `python-version` pin）のため
`requires-python = ">=3.10"` の床は依然 CI 未検証 — `python-quality` job の matrix 化（3.10/3.12）を
governance 別 PR で追う。② 実 py3.10 での最終確定は Jetson 上の pytest（真の runtime ゲート・#563 DoD）。
③ pydantic 実体は board では pip `--user` の v2（apt `python3-pydantic` は v1.8.2 で不適合）＝
`package.xml` の `python3-pydantic` exec_depend（2 pkg）との宣言不一致（同族: `typing_extensions` も rosdep 宣言なし＝jammy apt 3.10.0.2 は `Self` 非搭載・pip の pydantic v2 推移的依存で充足）は未解決の残として本追記に記録する（distro ドリフト台帳とは別軸）。④ AST 床ガードは **import 名の面のみ** — 3.11+ 構文（py3.12 の parse では素通り）・メソッド/挙動差（`Task.cancelling()`・`datetime.fromisoformat` の 3.11 拡張受理 等）は捕捉できず、**py3.10 実走（CI matrix ①＋ボード pytest ②）が唯一のオラクル**。

**追加発見（同日・実 py3.10 pre-verify）**: dev Mac に uv で py3.10 venv（CI `python-quality` job と
同一パッケージ集合）を作り全 suite を実行したところ、`datetime.UTC`（py3.11+ の `timezone.utc` alias）
の import が 4 ファイル（`warehouse_safety/emergency_guardian.py`・`warehouse_state/state_cache.py`・
`warehouse_llm_bridge/robotics/composition/record.py`・`tests/unit/test_composition_record.py`）で
18 テストモジュールの collection を落とすことが判明（StrEnum と同クラス＝「実 py310 でしか出ない」第 2 波）。
同じ機構で解消: `compat.UTC`（3.11+ = `datetime.UTC` re-export / 3.10 = `timezone.utc`＝**同一 singleton・
identity で不変**）を追加し 4 ファイルを sweep、`from datetime import UTC` / `datetime.UTC` の直書きも
source-scan で恒久禁止。L2 安全系 2 ファイル（Emergency Guardian / State Cache）は **import 行のみ**の
変更で挙動不変（既存 R-26 unit が検証）。影響トラックは llm-bridge に加え **safety-state** へも予告。
