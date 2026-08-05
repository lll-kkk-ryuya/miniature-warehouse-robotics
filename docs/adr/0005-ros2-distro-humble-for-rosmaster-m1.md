# ROS 2 distro を Jazzy から Humble へ切り替える（Isaac ROS on Orin ＋ ROSMASTER M1 採用に伴う）

**Status**: accepted（2026-08-05 user approval。ただし Gazebo の扱いのみ未決＝下記 Open）

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

**適用済み（13 ファイル）**: `pyproject.toml`（`target-version` py312 → **py310**）／`deploy/dev/run-sim-cockpit.sh`（イメージ名 `mwr-sim:humble`）／`deploy/jetson/bin/ros-exec.sh`（`ROS_DISTRO` 既定 humble）／docs 各所（architecture 03 / 06 / 12 / 16、jetson 01、setup/jetson-deploy、shared 02 / 04 / 09、adr/README）。

**未適用（53 ファイル）**。特に **現時点で整合が壊れている組み合わせ**:

| 箇所 | 状態 | 症状 |
|---|---|---|
| `deploy/dev/Dockerfile` | **jazzy のまま** | `run-sim-cockpit.sh` は `mwr-sim:humble` を探すが Dockerfile は jazzy を入れる → **中身が jazzy の `humble` タグ**が焼かれる。**最優先で解消**。 |
| `deploy/dev/run-mode-a-live.sh` / `install-nav2-e2e.sh` | jazzy のまま | cockpit と distro が食い違う |
| `firmware/platformio.ini` / `firmware/spike/**` | jazzy のまま | micro-ROS 側の distro 不一致 |
| `README.md` / `AGENTS.md` / `.claude/CLAUDE.md` | jazzy のまま | 新規セッションが Jazzy 前提で判断してしまう |

`# TODO(次スライス)` 上表を解消するまで **sim cockpit（Phase 0.5）と Jetson デプロイ経路は信頼できない**。`pyproject.toml` の py310 化だけが先行しているため、**lint の対象バージョンだけが黙って変わっている**点にも注意。`.claude/CLAUDE.md` は governance 所有のため、人間が別 PR で更新すること（`.claude/rules/parallel-workflow.md` §7.1）。

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
