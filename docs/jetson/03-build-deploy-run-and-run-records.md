# ビルド・デプロイ・実行の分離と記録（build-info v0 / run-record v0）

作成日: 2026-09-10

> **対象**: Jetson（`minicar`）上の colcon ワークスペース `/opt/warehouse/ws` を **SOURCE → BUILD → DEPLOY → RUN → DATA** に分けて扱い、
> **どのソースから作られた install space が動いているのか**（`mwr-build-info.v0`）と **1 回の走行で何が起きたのか**（`mwr-run-record.v0`）を
> 機械可読に残すための正本。**本 doc が正本**であり、`deploy/jetson/bin/` のスクリプトと `.claude/rules/build-deploy-run.md` は本 doc に従う
> （食い違ったら **code / rule を本 doc に合わせる**。本 doc が誤り・不足なら先に docs PR）。
>
> **layer**: ホスト側 build/deploy tooling（`deploy/jetson/bin/`）は [productization/01:174](../productization/01-commercial-box-map.md) のレイヤ annotation 対応表に
> **行が無い＝帰属未定**（[productization/01:194](../productization/01-commercial-box-map.md)「帰属未定の component は未定と書く」に従い断定しない）。確定しているのは**性質**の方で、
> 両スクリプトとも **actuation 経路を持たない**——node を起動せず `/bot{n}/cmd_vel`（[architecture/03:88](../architecture/03-software-architecture.md)）を publish せず systemd unit を
> enable / start / restart しない。走行記録は横断**観測面**（[productization/01:188](../productization/01-commercial-box-map.md)）に属する読み取り専用の記録で、**停止権限を持たない**。
>
> **設計正本（着手前 Read 済・file:line。全件は末尾 References）**: prod デプロイ＝[setup/jetson-deploy.md:44-58](../setup/jetson-deploy.md)（§2 タグ）/ [:60-65](../setup/jetson-deploy.md)（§3 ビルド）/ [:77-91](../setup/jetson-deploy.md)（§5 導入・enable/start しない）/ [:137-147](../setup/jetson-deploy.md)（§8 切替）。
> 別マシン＝clone: [architecture/17:88](../architecture/17-development-workflow.md) ／ prod＝git タグ: [architecture/19:118](../architecture/19-environments-and-config.md) ／ board 実測: [jetson/02:349](02-remote-access-and-dev-link.md) ／
> **別物として分ける相手**: [productization/09:153-162](../productization/09-run-manifest-and-plugin-composition.md)（`run_manifest.v1`）／ 凍結契約: `ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:18`（`MAX_LINEAR_VELOCITY = 0.3`）

---

## 0. 位置づけ — Build ≠ Deploy ≠ Run

| 段 | 何をするか | どこで起きるか | 残る物 |
|---|---|---|---|
| **SOURCE** | コードを書く・PR を出す | Mac の worktree（[architecture/17:88](../architecture/17-development-workflow.md)：同一マシンは worktree） | git commit / PR |
| **BUILD** | ソースから install space を作る | **ボード**（`/opt/warehouse/ws`。Jetson は別マシン＝clone） | `ws/install/` `ws/build/` `ws/log/`＋**ビルド記録**（§1） |
| **DEPLOY** | 動いている構成を新しい成果物へ**切替える** | ボード（`install.sh` → `systemctl restart`＝[setup/jetson-deploy.md:137-147](../setup/jetson-deploy.md) §8） | systemd unit の世代交代 |
| **RUN** | node を起動して実際に走らせる | ボード（launch / systemd） | プロセス・トピック |
| **DATA** | 走行中に何が流れたかを残す | ボード（`/ssd/bags`） | **走行記録**（§3）＋ rosbag2 |

CI（`.github/workflows/ci.yml`）は **BUILD をしない**——Ruff と pytest を py3.10/3.12 matrix で回すだけで、pytest は `ws/src` を直接 import する
（`pyproject.toml` の `testpaths=["tests"]` / `pythonpath=[".", "ws/src"]`）。**「CI が緑」は「ボードでビルドが通る」を意味しない**——それを知る唯一の場所がボードであり、記録するのが §1。

### 3 つを混ぜない理由

- **build = 成果物を作る**。install space を書き換えるだけで、動いているプロセスには触れない。
- **deploy = 切替える**。`install.sh` で unit を入れ直し `systemctl restart` で載せ替える工程
  （[setup/jetson-deploy.md:137-147](../setup/jetson-deploy.md)）。**build.sh は絶対にここへ踏み込まない**（§1 安全ガード）。
- **run = 起動する**。走行中に build すると**走っているプロセスの足元の install space が書き換わる**——だから §1 は既定で拒否する。

> ボードは `install.sh` すら**未実施**（unit 未導入・[setup/jetson-deploy.md:93-95](../setup/jetson-deploy.md) の意図的 defer / [jetson/02:355](02-remote-access-and-dev-link.md)）
> ＝現状は **BUILD と RUN（手起動 launch）しかなく DEPLOY 段は空**。それでも 3 つを分けておくのは、`v0.1.0` 発行後に DEPLOY が実在した瞬間に記録の形を変えずに済ませるため。

### ビルドの実体と「再ビルドが要る変更」

ボードの 17 パッケージは**すべて `ament_python`**（`ws/src/*/package.xml` の `<build_type>` を実測・2026-09-10。doc16 のツリーでも
全パッケージが `[ament_python]`＝[architecture/16:28-43](../architecture/16-repository-and-conventions.md)）。C++ のコンパイルは無く、
`colcon build` がやるのは主に **entry point と data files の登録**。さらに dev profile は `--symlink-install` を使い、ボードの
install space には `warehouse-m1-driver.egg-link` のような **develop 形式のリンク**が置かれている（実測 2026-09-10）。ROS 2 公式（Humble）は
`--symlink-install` を「source space の file を変えれば install された file も変わる」と説明しており、**既存 `.py` の編集は再ビルド無しで反映される**。

一方、公式 docs は「どの変更が再ビルドを要するか」を**列挙していない**。下表の大半は **機構からの推論**であり、**確証があるのは 2 行だけ**。
迷ったら**再ビルドする**（コストは数十秒、誤ると「直したのに直らない」を延々追うことになる）。

| 変更 | dev（`--symlink-install`） | prod（素の `colcon build`） | 出典 |
|---|---|---|---|
| 既存 `.py` の編集 | **不要** | **要** | **公式 docs に明記**（`--symlink-install` の意味 / 素の build は install 時コピー） |
| 新しい `.py` を既存 package に追加 | ほぼ不要（develop リンク越しに見える） | 要 | 機構からの推論 |
| `setup.py` の `entry_points`（新 node・console_scripts） | **要** | 要 | 機構からの推論 |
| `launch/` `config/` `rviz/` 等の data_files 追加・改名 | **要** | 要 | 機構からの推論 |
| `package.xml` の依存追加 | **要**（＋§2 rosdep） | 要 | 機構からの推論 |
| 新規 package の追加 | **要** | 要 | **公式 docs に明記**（`colcon build` が package を検出して install space を作る） |
| `.gitignore` / docs のみ | 不要 | 不要 | 機構からの推論 |

---

## 1. ビルド記録（`mwr-build-info.v0`）

| 物 | 置き場所 |
|---|---|
| 現行 build | `<ws>/install/.mwr-build-info.json` |
| 履歴 | `<ws>/log/build-info/<build_id>.json` |
| dirty 差分 | `<ws>/log/build-info/<build_id>.diff` |

- `<ws>` = `/opt/warehouse/ws`（env `WAREHOUSE_WS` があればそれ・`--ws` で上書き）。`install/` `log/` は **gitignore 済み領域**
  （[architecture/16:165-169](../architecture/16-repository-and-conventions.md)）＝記録がリポジトリを汚さない。`profile` = `dev` | `prod`。
- `build_id` = `<sha7>-<YYYYMMDDTHHMMSS>-<profile>`（例 `c79d34c-20260910T125205-dev`）。**git SHA と `build_id` は別概念（1 対多）**
  ——同じ SHA を profile 違い・時刻違いで何度でも build するため、「どの SHA か」だけでは走行を同定できない。

**スキーマ**（JSON・キー順固定・`indent=2`・`ensure_ascii=False`・末尾改行）:

```json
{
  "schema": "mwr-build-info.v0",
  "build_id": "c79d34c-20260910T125205-dev",
  "profile": "dev",
  "built_at": "2026-09-10T12:52:05+09:00",
  "source": {
    "git_sha": "<40 hex>",
    "git_sha_short": "c79d34c",
    "ref": "main",
    "tag": null,
    "untagged_override": false,
    "dirty": false,
    "untracked_count": 0,
    "diff_sha256": null,
    "diff_path": null
  },
  "host": {
    "hostname": "minicar",
    "ros_distro": "humble",
    "l4t": "R36.4.4",
    "kernel": "5.15.148-tegra",
    "python": "3.10.12"
  },
  "deps": {
    "rosdep_check": "ok",
    "unsatisfied": [],
    "pip_exceptions": ["python3-pydantic"]
  },
  "colcon": {
    "args": ["--symlink-install"],
    "packages": 17,
    "exit_code": 0,
    "log_dir": "log/build_2026-09-10_12-52-05",
    "duration_s": 18.7,
    "forced": false
  },
  "deployment": null
}
```

- `built_at` — ISO 8601 ローカル時刻＋オフセット（`datetime.now().astimezone().isoformat(timespec="seconds")`）。
- `source.ref` — `git rev-parse --abbrev-ref HEAD`（detached なら `HEAD`）。`source.tag` — `git describe --exact-match --tags` の値、無ければ `null`。
- `source.dirty` — `git status --porcelain --untracked-files=no` が非空。`untracked_count` — `--untracked-files=all` の `??` 行数。
- **dirty のとき** — `git diff HEAD`（tracked のみ）を `<ws>/log/build-info/<build_id>.diff` に保存し、その sha256 を `diff_sha256`、`<ws>` 基点の
  相対パスを `diff_path` に書く。clean なら両方 `null`。**「dirty だった」で終わらせず、何が違ったかを後から出せる**ようにする。
- `host.l4t` — `/etc/nv_tegra_release` の `# R36 (release), REVISION: 4.4` から `R36.4.4` を組む。取れなければ `null`。
  `ros_distro` — env `ROS_DISTRO`、無ければ `/opt/ros/<x>` の唯一の候補、それも無ければ `null`。
- `deps` — §2 のとおり `rosdep check`（read-only）の結果。値は `ok` | `unsatisfied` | `skipped` | `error`（rosdep は走ったが鍵を解決できない＝`ROS_DISTRO` 未設定など。`unsatisfied` は空）。rosdep には常に `ROS_DISTRO` を渡す（`build.sh` は underlay を source・直接実行時は `/opt/ros/<x>` から補完）。
- `colcon.args` — dev は `["--symlink-install"]`、prod は `[]`。`packages` = `colcon list --names-only` の行数、取れなければ `src/*/package.xml` の数に fallback。
  `log_dir` = `<ws>/log/latest_build` の symlink 解決先を `<ws>` 基点の相対で。`duration_s` は小数 1 桁。
- `colcon.forced` — 安全ガード（motion stack 稼働中）を `--force` で乗り越えて build したとき `true`、平時は `false`。**forced build も build-info を書く**——install space が実際に変わった以上、記録しない方が嘘になる。
- `deployment` — **予約フィールド（常に `null`）**。別マシンで build した成果物を配布する運用になったとき
  `{deployment_id, robot_id, artifact_sha256, units[]}` を入れる。**今はロボット上で build するので build = deploy**——「省略」ではなく「予約」。

**profile の違い（prod は締める）**:

| | dev | prod |
|---|---|---|
| colcon | `colcon build --symlink-install` | 素の `colcon build`（[setup/jetson-deploy.md:64](../setup/jetson-deploy.md) と同じコマンド） |
| dirty | 許可（差分を保存） | **拒否** |
| tag | 不問 | **`git describe --exact-match --tags` が無ければ拒否** |

prod の tag 必須は「prod デプロイは git タグ」（[architecture/19:118](../architecture/19-environments-and-config.md) /
[setup/jetson-deploy.md:46](../setup/jetson-deploy.md)）の機械化。ただし `v0.x` は**未発行**で bring-up 中は main HEAD 直 clone 運用
（[setup/jetson-deploy.md:54-58](../setup/jetson-deploy.md) / [jetson/02:349](02-remote-access-and-dev-link.md)）なので、逃げ道として
**`--allow-untagged`** を置く：続行するが `source.tag=null` かつ **`source.untagged_override=true`** を記録し、「タグ無しで prod build した」
事実を消さない。**`v0.1.0` 発行までの暫定**であり、発行後はこのフラグを使わない。

### 安全ガード（build ≠ deploy ≠ run の強制）

- **走行中は拒否**（exit 3）: `systemctl is-active warehouse.target` が active、または `pgrep -f warehouse_m1_driver` /
  `pgrep -f "ros2 launch warehouse_bringup"` が hit したとき。**`--force` はガードを外すだけ**で記録は通常どおり残り、
  **`colcon.forced=true`** で区別される（stderr 警告つき）＝走行中に書き換えた事実を消さない。forced build を使ったら**報告で明示する**。
  `systemctl` / `pgrep` が無い環境（Mac）は「不明」として続行。
- **`systemctl restart` / `enable` / `start` を絶対に呼ばない**。切替は [setup/jetson-deploy.md:137-147](../setup/jetson-deploy.md) §8 の
  別工程であり、`install.sh` が enable/start しない設計（[setup/jetson-deploy.md:87](../setup/jetson-deploy.md)）と同じ規律。
- **sudo を使わない・ssh を使わない**。

### コマンドと、記録が顔を出す場所

```bash
deploy/jetson/bin/build.sh [--profile dev|prod] [--force] [--skip-rosdep] [--allow-untagged] [--dry-run]
```

（このほか `--colcon-cmd "<cmd>"` / `--rosdep-cmd "<cmd>"` があるが、**外部コマンドを差し替えるテスト用の seam** でありオペレータ向けフラグではない。）

| exit | 意味 |
|---|---|
| 0 | 成功 |
| 1 | `colcon` 失敗 |
| 2 | prod ポリシー違反（dirty・untagged） |
| 3 | 安全ガード（走行中） |
| 4 | usage（`<ws>` が git work tree でない・不在を含む） |

`--dry-run` は colcon / rosdep を走らせず、書く予定の JSON を stdout に出す（git 情報は実際に読む）。このとき `colcon.exit_code` / `duration_s` /
`log_dir` は `null`・`packages` は `src/*/package.xml` の数・`deps.rosdep_check="skipped"`・`forced=false` となり、**ファイルは何も書かない**。書いた記録は 2 箇所に出す:

- `deploy/jetson/bin/preflight.sh` の `run_arrival_checks`（`--arrival`）で、
  `workspace install/setup.bash exists` の直後に **build-info の有無**（無ければ warn）と **rosdep check**（§2・read-only）を足す。
- Mac 側 `jetson status`（`deploy/dev/jetson-link/jetson` の `cmd_status` → `readiness` が回す remote PROBE の `kv build` 行）。現状 probe は
  `git rev-parse --short HEAD` しか出さない＝**「どの SHA が置いてあるか」は分かるが「その SHA で build したか」は分からない**。`build_id` がその穴を塞ぐ。

---

## 2. 依存充足（rosdep は read-only で見るだけ）

build 前に `rosdep check --from-paths <ws>/src --ignore-src` を**実行して記録するだけ**にする（install はしない）。理由は 2 つ——
apt install は sudo を要する（§1 の禁止）ことと、**依存不足は「build の失敗」より先に「起動時の import エラー」として出る**ため、
記録しておかないと切り分けが遅れること。非 0 のときは `System dependencies have not been satisfied:` 以降の `apt\t<pkg>` 行を
`deps.unsatisfied` に列挙する。`rosdep` 不在または `--skip-rosdep` なら `deps.rosdep_check="skipped"`。鍵を解決できない出力（`Cannot locate rosdep definition`）は `"error"`＝ほぼ `ROS_DISTRO` 未設定（実測 2026-09-10: 未設定だと ROS 鍵が全滅し pydantic だけが残る）。`preflight.sh --arrival` は env ファイルの `ROS_DISTRO` を rosdep に渡し、解決失敗は「未充足」と区別して WARN する。

**ボード実測の 6 件（2026-09-10）**:

| apt key | 使う所 | 扱い |
|---|---|---|
| `ros-humble-ros-gz-sim` | Gazebo sim | **ロボット上では不要**（sim は Mac Docker 側） |
| `ros-humble-ros-gz-bridge` | Gazebo sim | 同上 |
| `ros-humble-rviz2` | GUI 可視化 | **ロボット上では不要** |
| `ros-humble-xacro` | `warehouse_description/launch/description.launch.py` | **description を起動する段で要**。導入は人間の sudo |
| `ros-humble-twist-mux` | `warehouse_bringup/launch/bringup.launch.py`・`nav2_bringup.launch.py` | **Phase 1 standalone teleop では不要**（twist_mux を立てない＝[mode-m1/03:50](../mode-m1/03-joystick-teleop-bringup.md)）。**Nav2 同時稼働スライスの前に要** |
| `python3-pydantic` | 契約 schema（`warehouse_interfaces`） | **pip 例外**（下記） |

`deps.pip_exceptions` は「rosdep が見ない pip 導入物」の固定リスト（v0 では `["python3-pydantic"]`）。**`unsatisfied` からは除かない**
——機械が観測した生の結果を歪めず、**読み替えは下の pip 例外表で行う**。件数が増えたら本表と併せて更新する。

| rosdep key | 実体 | なぜ `unsatisfied` に出るか |
|---|---|---|
| `python3-pydantic` | **pip で導入済み**（ボード実測 2026-09-10） | rosdep は apt しか見ないため pip 導入物を検出できない |

**実際に入れるとき（人間の sudo）**: Humble 公式チュートリアルの形は `rosdep install --from-paths src -y --ignore-src`
（`-r` は付けない＝「エラーが出ても続行」で失敗を握り潰すため）。**xacro と twist_mux は Nav2 段に入る前に入れておく**。

---

## 3. 走行記録（`mwr-run-record.v0`）

**ディレクトリ（`<bags_dir>/<run_id>/`）**:

```
run-record.json
rosbag2/                 ← ros2 bag record -o <dir>/rosbag2 …（metadata.yaml と *.db3 or *.mcap）
runtime/nodes.txt        ← ros2 node list の生出力
runtime/topics.txt       ← ros2 topic list -t の生出力
parameters/<node>.yaml   ← ros2 param dump <node>（node 名の "/" は "_" に置換。best-effort）
calibration/hashes.json  ← {"files": [{"path": "<絶対パス>", "sha256": "<64 hex>"}]}
notes.md                 ← --notes "<text>" 指定時のみ
```

- `bags_dir` 既定 `/ssd/bags`（`--bags-dir` で変更）。`/ssd` は 916GB 中 870GB 空き（実測 2026-09-10）。
- `run_id` = `YYYYMMDD-NNN`（ローカル日付・同日連番 3 桁・`bags_dir` の既存ディレクトリから採番）。`--slug foo` で `YYYYMMDD-NNN-foo`。

**スキーマ**:

```json
{
  "schema": "mwr-run-record.v0",
  "run_id": "20260910-001",
  "robot_id": "bot1",
  "started_at": "2026-09-10T15:00:00+09:00",
  "ended_at": null,
  "duration_s": null,
  "operator": null,
  "purpose": null,
  "source": { "...": "build-info.source をそのままコピー（build-info 不在なら null）" },
  "build": {
    "build_id": "c79d34c-20260910T125205-dev",
    "profile": "dev",
    "built_at": "2026-09-10T12:52:05+09:00",
    "build_info_path": "/opt/warehouse/ws/install/.mwr-build-info.json"
  },
  "firmware": { "version": null, "car_type": null, "source": null, "binary_sha256": null },
  "launch": { "entrypoint": null, "args": [] },
  "runtime": { "nodes": "runtime/nodes.txt", "topics": "runtime/topics.txt", "captured_at": null, "capture_delay_s": 5.0 },
  "parameters": { "snapshot_dir": "parameters/", "nodes_dumped": [], "dump_errors": {}, "parameter_events_recorded": true },
  "calibration": { "index": "calibration/hashes.json", "count": 0 },
  "safety": { "max_linear_velocity_mps": 0.3, "source": "warehouse_interfaces.safety.MAX_LINEAR_VELOCITY", "car_type": null },
  "data": { "storage": "sqlite3", "bag": "rosbag2/", "record_mode": "all", "topics": null, "bag_exit_code": null }
}
```

- `build` / `source` — `<ws>/install/.mwr-build-info.json` から読む。**無ければ `build=null`・`source=null`** とし、stderr に
  「build-info が無い。`deploy/jetson/bin/build.sh` で build せよ」と警告する。**拒否はしない**——記録が無いことを理由に走行を止めると、
  記録の付かない走行が「記録の外」で行われるようになるため。
- `robot_id` — 既定 `bot1`。doc03 の `/bot{n}` 名前空間（[architecture/03:75-88](../architecture/03-software-architecture.md)）に
  合わせるだけで、**新しい env 変数は作らない**（`--robot-id` で上書き）。
- `firmware.version` / `car_type` — v0 では **CLI の手入力**（`--fw-version 3.6 --car-type 10`）。指定時 `source="cli"`。
  `binary_sha256` は予約（常に `null`）＝**読めないものを読めたことにしない**。
- `launch.entrypoint` — `--launch "<コマンド文字列>"` の写し。**record-run 自身は何も起動しない・何も publish しない（観測のみ）**。
- `runtime` — bag 開始から `capture_delay_s`（既定 `5.0`・`--capture-delay`）後に `ros2 node list` / `ros2 topic list -t` を取り、`captured_at` を書く。値は **float**＝短い走行を潰さないよう `--capture-delay 0.5` のような秒未満も渡せる。
- `parameters` — 取得した node ごとに `ros2 param dump <node>` を保存。失敗は `dump_errors[node] = "<stderr 先頭 200 字>"`。
  `parameter_events_recorded` は `record_mode == "all"` なら true、`topics` 指定時は `/parameter_events` が含まれていれば true。
- `safety.max_linear_velocity_mps` — 凍結契約 `warehouse_interfaces.safety.MAX_LINEAR_VELOCITY`
  （`ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:18` ＝ `0.3`）を **import して書く**（値を写経しない）。
  import 失敗時は `null` と `source="unavailable"`。`car_type` は `firmware.car_type` の写し。
- `data.storage` — 既定 `sqlite3`。**rosbag2 Humble の既定 plugin が sqlite3**（MCAP 既定は Iron 以降）で、ボードには
  `rosbag2-storage-default-plugins` しか入っていない（実測 2026-09-10）。**`--storage mcap` を使ってよいのは
  `ros-humble-rosbag2-storage-mcap` 導入後だけ**（format 採否自体は要 spike＝[productization/05:406](../productization/05-decision-observability-and-tooling.md)）。
- `data.record_mode` — 既定 `all`（`ros2 bag record -a`＝`/parameter_events` と `/rosout` も掴む）。`--topics "/a /b"` 指定で `topics` になり配列を記録。
  **`ros2 bag record` へ topic は位置引数で渡す**（Humble に `--topics` は無い）。record-run 自身の `--topics "/a /b"` という受け口の表記は据え置き。
- **終了** — SIGINT / SIGTERM または `--duration <s>` で bag プロセスへ SIGINT → 待機 → `ended_at`・`duration_s`・`bag_exit_code` を書く。
  json は開始直後に一度書き、以後は上書き（atomic: tmp → rename）＝**途中で電源が落ちても「走った」記録は残る**。
- **bag を孤児にしない** — bag 起動後の処理は `try/finally` で必ず停止処理（SIGINT → 20 s → SIGTERM → 10 s）に到達し、
  SIGINT/SIGTERM ハンドラは **bag 起動より前**に登録する。capture や JSON 書出しが失敗しても、記録プロセスだけ死んで bag が走り続ける状態を作らない。

### run-record は `run_manifest.v1` ではない

[productization/09:153-162](../productization/09-run-manifest-and-plugin-composition.md) の `run_manifest.v1` は **L3 の plugin 合成を
宣言する bridge-local artifact** で、unknown な `schema_version` を fail-closed で reject する。本 doc の `mwr-run-record.v0` は
**ボード上のホスト tooling が観測を綴じるだけ**の別物であり、schema 名（`mwr-` 接頭辞）・置き場所（`/ssd/bags/<run_id>/` vs
`out/runs/<run_id>/`＝[productization/09:48](../productization/09-run-manifest-and-plugin-composition.md)）・所有者のすべてを分ける。
**片方をもう片方の入れ物に流用しない。**

**コマンド**:

```bash
deploy/jetson/bin/record-run.sh [--bags-dir DIR] [--ws PATH] [--robot-id bot1] [--slug NAME] \
    [--operator NAME] [--purpose TEXT] [--launch "<cmd>"] [--fw-version V] [--car-type N] \
    [--topics "/a /b"] [--storage sqlite3|mcap] [--capture-delay 5] [--duration S] \
    [--calibration FILE ...] [--notes TEXT] [--dry-run]
```

（`--ros2-cmd "<cmd>"` もあるが、`ros2` 実体を差し替える**テスト用の seam** でありオペレータ向けフラグではない。）

| exit | 意味 |
|---|---|
| 0 | 正常 |
| 4 | usage（`ros2` 不在＝ディレクトリを作る前に終了・`bags_dir` が作れない） |

### 拡張点（予約）

v0 で**入れないが、後から入れられるように場所だけ空けてある**もの。schema を割らずに埋められる。

| 将来やりたいこと | 予約しているフィールド |
|---|---|
| dirty build の差分を後から同定する | build-info `source.diff_sha256` / `source.diff_path`（dirty 時のみ実体） |
| 同一 SHA の複数 build を区別する | `build_id`（`<sha7>-<時刻>-<profile>`）を `source.git_sha` と分離 |
| 別マシンで build → 配布する運用 | build-info `deployment`（常に `null`。`{deployment_id, robot_id, artifact_sha256, units[]}` を想定） |
| runtime graph のスナップショット | run-record `runtime.nodes` / `topics` / `captured_at` |
| parameter スナップショット | `parameters.snapshot_dir` / `nodes_dumped` / `dump_errors` ＋ `/parameter_events`（`parameter_events_recorded`） |
| calibration の同定 | `calibration.index` / `count`（`--calibration` 指定分の sha256） |
| firmware バイナリの同定 | `firmware.binary_sha256`（常に `null`。v0 は `version` / `car_type` の手入力のみ） |

---

## 4. テストの位置づけ

| 何を | どこで | 位置づけ |
|---|---|---|
| `pytest tests/unit`（host） | Mac / CI | CI matrix **py3.10 / py3.12**。本 tooling の unit は `tests/unit/` に置く（`testpaths=["tests"]` ゆえ `ws/src/*/test/` は CI から見えない） |
| ボード pytest | Jetson・**ROS を source していないシェル** | py3.10 実走の**唯一のオラクル**（[ADR-0008:157](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) / [:183](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)）。非 source の理由は旧 ROS pytest plugin との衝突（[shared/02:866](../shared/02-hardware-design.md) / [:876](../shared/02-hardware-design.md)＝**正本 doc 未定の運用メモ**） |
| `colcon test` | ボード | **Phase 3**（[architecture/20:76](../architecture/20-dev-quality-and-testing.md)）。v0 の範囲外 |
| CI での `colcon build` | — | 未導入（§6） |

スクリプト本体は **py3.10 互換**（`match` 文・`tomllib`・`typing.Self`・`ExceptionGroup` を使わない）・**stdlib のみ**・型ヒント必須。
bash ラッパは **bash 3.2 で構文が通る**こと（`declare -A`・`${x,,}`・`mapfile` を使わない）。ボードの Python は 3.10.12、
Ruff の `target-version` は `py310`（`pyproject.toml:33`）。

---

## 5. 1 サイクルの手順

**dev（bring-up 中の通常運転）**:

```bash
cd /opt/warehouse && git fetch origin && git checkout <ref>   # 1. ソース更新（ボード上）
deploy/jetson/bin/build.sh --profile dev                      # 2. ビルド＋記録
deploy/jetson/bin/preflight.sh --arrival                      # 3. 到着チェック（build-info / rosdep がここに出る）
# 4. 物理準備: 静止・車輪を浮かせる（下記）    5. launch（別ターミナル）
deploy/jetson/bin/record-run.sh --purpose "M2 negative test" --launch "<手順5のコマンド>"   # 6. 記録開始（観測のみ）
# 7. Ctrl-C で終了 → /ssd/bags/<run_id>/run-record.json を確認
```

**手順 4 は省略しない**。W-3（MCU watchdog）が不在である以上、W-4（運用層＝車輪を浮かせる・主電源に手を掛ける）が必須の層である
（[mode-m1/02:60-65](../mode-m1/02-m1-driver-and-watchdog.md) W-1〜W-4・[mode-m1/02:70](../mode-m1/02-m1-driver-and-watchdog.md) §4
「床走行では絶対にやらない」）。M0/M1/M2 の 3 段ゲートは [mode-m1/03:8-12](../mode-m1/03-joystick-teleop-bringup.md) が正本。

**prod（`v0.1.0` 発行後）**:

```bash
cd /opt/warehouse && sudo git fetch --tags && sudo git checkout v0.y
deploy/jetson/bin/build.sh --profile prod          # dirty / tag 無しは拒否
sudo /opt/warehouse/deploy/jetson/bin/install.sh   # unit 差分反映
sudo systemctl restart warehouse.target            # ← 切替は build.sh の外
```

切替（3 行目）は [setup/jetson-deploy.md:137-147](../setup/jetson-deploy.md) §8 が正本。
そもそも `systemctl enable --now` は **§0 安全ゲート通過後のみ**（[setup/jetson-deploy.md:26](../setup/jetson-deploy.md)）。

---

## 6. やらないこと・未決

| 項目 | 状態 |
|---|---|
| CI で `colcon build` / `colcon test` を回す | **やらない**（`colcon test` は Phase 3＝[architecture/20:76](../architecture/20-dev-quality-and-testing.md)）。CI 追加は governance の別 PR |
| ROS スタックを Jetson 上で Docker 化 | **やらない**。prod は native systemd（[setup/jetson-deploy.md:151-159](../setup/jetson-deploy.md) の unit 一覧）。Docker は稼働しているが data-root が `/ssd` の別用途（[jetson/02:347](02-remote-access-and-dev-link.md)） |
| Isaac ROS の導入 | **やらない**（ボードに 0 パッケージ・導入予定なし。Humble 用 3.2 の cuVSLAM に depth 入力経路が無い＝[architecture/23:440](../architecture/23-perception-and-localization.md) の blocked 判断） |
| fake / モータ無し runner | **v0 では作らない**。seam は既にある（`warehouse_m1_driver` の `MotionBackend` Protocol）が、M1 ゲートは read-only プローブ＋**車輪を浮かせる**で担保する |
| MCAP | plugin（`ros-humble-rosbag2-storage-mcap`）導入まで **`--storage mcap` を使わない**。format 採否は要 spike（[productization/05:406](../productization/05-decision-observability-and-tooling.md)） |
| `v0.1.0` タグ | **未発行**。§5 prod 手順と `--allow-untagged` の廃止はこれ待ち（[setup/jetson-deploy.md:54-58](../setup/jetson-deploy.md)） |
| `robot_id` の正本 | **未決**。v0 は doc03 の `/bot{n}` に合わせた既定値 `bot1` を置くだけで、config / env の正本化はしない |
| firmware バイナリの同定 | **未決**（`binary_sha256` は予約のまま。version / car_type は手入力） |
| `deployment` の実体化 | **未決**（build = deploy である限り `null`。別マシン build を始める時に設計する） |
| 新語彙（`driver lease` / `operation_manager` 等） | **導入しない**（[GLOSSARY.md](../GLOSSARY.md) に無い語を発明しない） |
| `deps.pip_exceptions` の一般化 | **未決**。v0 は固定 1 件。pip 導入物が増えたら §2 の表と併せて更新する |

---

## References

- prod デプロイ正本: [docs/setup/jetson-deploy.md:44-58](../setup/jetson-deploy.md)（§2 タグ・bring-up 暫定）/ [docs/setup/jetson-deploy.md:60-65](../setup/jetson-deploy.md)（§3 ビルド）/ [docs/setup/jetson-deploy.md:77-91](../setup/jetson-deploy.md)（§5 導入・enable/start しない）/ [docs/setup/jetson-deploy.md:137-147](../setup/jetson-deploy.md)（§8 切替）/ [docs/setup/jetson-deploy.md:26](../setup/jetson-deploy.md)（安全ゲート）
- ボード実測: [docs/jetson/02-remote-access-and-dev-link.md:343-350](02-remote-access-and-dev-link.md)（§9.6 provisioning・row5）/ [docs/jetson/02-remote-access-and-dev-link.md:355](02-remote-access-and-dev-link.md)（unit 未 install）／ ゲート: [docs/jetson/01-fidelity-and-validation.md](01-fidelity-and-validation.md)（G0-G7）
- 環境・構成: [docs/architecture/17-development-workflow.md:75-88](../architecture/17-development-workflow.md)（別マシン＝clone）/ [docs/architecture/19-environments-and-config.md:6](../architecture/19-environments-and-config.md) / [docs/architecture/19-environments-and-config.md:118](../architecture/19-environments-and-config.md)（prod＝タグ）/ [docs/architecture/16-repository-and-conventions.md:24-52](../architecture/16-repository-and-conventions.md)（ツリー）/ [docs/architecture/16-repository-and-conventions.md:165-169](../architecture/16-repository-and-conventions.md)（生成物 gitignore）
- テスト・契約: [docs/architecture/20-dev-quality-and-testing.md:24-36](../architecture/20-dev-quality-and-testing.md)（ピラミッド）/ [docs/architecture/20-dev-quality-and-testing.md:76](../architecture/20-dev-quality-and-testing.md)（colcon test＝Phase 3）/ [docs/architecture/03-software-architecture.md:75-88](../architecture/03-software-architecture.md)（凍結トピック `/bot{n}/…`）/ [docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md:157](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md) / [docs/adr/0008-ros2-distro-humble-for-rosmaster-m1.md:183](../adr/0008-ros2-distro-humble-for-rosmaster-m1.md)（py3.10 実走＝唯一のオラクル）/ [docs/shared/02-hardware-design.md:866](../shared/02-hardware-design.md) / [docs/shared/02-hardware-design.md:876](../shared/02-hardware-design.md)（ボードは ROS 非 source シェル・正本未定）
- 別物・観測: [docs/productization/09-run-manifest-and-plugin-composition.md:42-65](../productization/09-run-manifest-and-plugin-composition.md) / [docs/productization/09-run-manifest-and-plugin-composition.md:153-162](../productization/09-run-manifest-and-plugin-composition.md)（`run_manifest.v1`＝**別物**）/ [docs/productization/05-decision-observability-and-tooling.md:406](../productization/05-decision-observability-and-tooling.md)（bag format 要 spike）/ [docs/productization/01-commercial-box-map.md:174](../productization/01-commercial-box-map.md) / [docs/productization/01-commercial-box-map.md:194](../productization/01-commercial-box-map.md)（layer 対応表・帰属未定）
- 走行ゲート: [docs/mode-m1/03-joystick-teleop-bringup.md:8-12](../mode-m1/03-joystick-teleop-bringup.md)（M0/M1/M2）/ [docs/mode-m1/03-joystick-teleop-bringup.md:50](../mode-m1/03-joystick-teleop-bringup.md)（standalone＝twist_mux なし）/ [docs/mode-m1/02-m1-driver-and-watchdog.md:60-65](../mode-m1/02-m1-driver-and-watchdog.md)（W-1〜W-4）/ [docs/mode-m1/02-m1-driver-and-watchdog.md:70](../mode-m1/02-m1-driver-and-watchdog.md)（G-g 車輪浮かせ）
- 用語: [docs/GLOSSARY.md](../GLOSSARY.md) §11「ビルド記録（build-info）」「走行記録（run record）」（双方向）
- 実装（コードは churn するので **symbol で指し `path:line` で固定しない**＝[.claude/rules/session-orchestration.md](../../.claude/rules/session-orchestration.md) §8）: `deploy/jetson/bin/build.sh` / `mwr_build.py` / `record-run.sh` / `mwr_run_record.py`、`deploy/jetson/bin/preflight.sh` の `run_arrival_checks`（arrival）、`deploy/dev/jetson-link/jetson` の `cmd_status` → `readiness`（status probe の `kv build` 行）／ 凍結契約 `ws/src/warehouse_interfaces/warehouse_interfaces/safety.py:18`／ 規約 `.claude/rules/build-deploy-run.md`（要点のみ・正本は本 doc）
