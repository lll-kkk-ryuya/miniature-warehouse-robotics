---
description: ボードの build / deploy / run を分離し、ビルド記録（build-info）と走行記録（run record）を必ず残す
---

# Build / Deploy / Run 分離とビルド・走行記録（build-deploy-run）

> 防ぐ事故: 「**pull したのに古い挙動**」「**今ロボットで何が動いているか不明**」「**走行データに版が紐付かない**」。
> 正本 = [docs/jetson/03-build-deploy-run-and-run-records.md](../../docs/jetson/03-build-deploy-run-and-run-records.md)（§0〜§6・schema と判定表）＋ [docs/setup/jetson-deploy.md](../../docs/setup/jetson-deploy.md) `:44`（§2 タグ）/ `:60`（§3 ビルド）/ `:77`（§5 導入）/ `:137`（§8 更新・ロールバック）＋ [docs/architecture/17-development-workflow.md:75-101](../../docs/architecture/17-development-workflow.md)。本書は**要点と参照のみ**（schema 表を複製しない）。

## 原則

- **Build ≠ Deploy ≠ Run**。Build = 成果物を作る（`colcon`）／ Deploy = ロボットの**実行版を切替える**（`install.sh` で unit 差分反映 → `systemctl restart`＝[docs/setup/jetson-deploy.md:137](../../docs/setup/jetson-deploy.md) §8）／ Run = 起動・走行（**記録する**）。3 つを 1 コマンド・1 手順に混ぜない。
- **Mac は worktree・Jetson は clone**（[docs/architecture/17-development-workflow.md:75-101](../../docs/architecture/17-development-workflow.md) §4.0）。prod は **git タグ固定**（[docs/architecture/19-environments-and-config.md:115](../../docs/architecture/19-environments-and-config.md) §6 / [docs/setup/jetson-deploy.md:44](../../docs/setup/jetson-deploy.md) §2）。`v0.1.0` 未発行の bring-up 中は main 直運用が暫定で許される（同 `:54` 補足）。
- **`ws/src` は全 17 pkg が `ament_python`。だが「build 不要」ではない**。`entry_points` / `setup.py` / `package.xml` / `data_files`（launch・config）／新規ファイル／`.msg` は**再ビルドが要る**。**迷ったら再ビルド**（判定表は jetson/03 §0）。
- **レイヤ**: この tooling は **L0'〜L4 の外**（正準対応表に行を持たない・帰属未定・actuation なし。build も record も motion を出さない）＝[layer-annotation.md](layer-annotation.md)。

## 必須

1. ボード（`/opt/warehouse`）では**素の `colcon build` を打たない** → **`deploy/jetson/bin/build.sh`**（dev = `--symlink-install` / prod = `--profile prod`＝clean tree ＋ tag 必須）。**ビルド記録（build-info）**は `ws/install/.mwr-build-info.json`（`mwr-build-info.v0`・履歴は `ws/log/build-info/`）。
2. build 前に **`rosdep check`**（read-only。build.sh が実行し記録に残す）。導入が要るなら `rosdep install -y --ignore-src` のみで、**`-r`（エラー無視）は付けない**。pip 由来の例外は **jetson/03 §2 の表にあるものだけ**（勝手に増やさない）。
3. **走行中に build しない**。build.sh は motion stack 稼働中を検出して拒否する＝**`--force` でガードを外さない**。build.sh は `systemctl restart` / `enable` / `start` を**呼ばない**（切替は [docs/setup/jetson-deploy.md:137](../../docs/setup/jetson-deploy.md) §8 の別工程・人間が sudo で行う）。
4. 実機を動かす走行は **`deploy/jetson/bin/record-run.sh`** で記録する（**観測のみ**・何も起動しない・全トピック `-a` 既定・`/ssd/bags/<run_id>/run-record.json`）。**記録の無い走行は「試した」とだけ言い、「確認した」と言わない**。
5. **走行記録（run record）`mwr-run-record.v0` は L3 の `run_manifest.v1` と別物**（[docs/productization/09-run-manifest-and-plugin-composition.md:153-159](../../docs/productization/09-run-manifest-and-plugin-composition.md) fail-closed・`extra=forbid`）。名前・schema 名・置き場所を混ぜない。**フィールド追加は schema 版を上げ、docs（jetson/03）を先行**させる。
6. ボードで動く Python は **py3.10 互換**（`pyproject.toml:33` `target-version = "py310"`＝Humble / Ubuntu 22.04）。マージゲートのテストは**ホストの `pytest tests/unit`**。`colcon test` は [docs/architecture/20-dev-quality-and-testing.md:76](../../docs/architecture/20-dev-quality-and-testing.md) の **Phase 3** 課題＝任意（CI に colcon を足すのは governance PR）。
7. セッション報告で「ボードの版」を語るときは **`jetson status` の出力**（build 行 = build_id / SHA / dirty）を示す。**記憶で言わない**（[docs-first.md:42](docs-first.md) §引用）。

## やってはいけない

- ボードで**素の `colcon build`** を打つ（記録が残らず「今動いている版」が不明になる＝本書が防ぐ事故そのもの）。
- **走行中に build** する／`--force` でガードを外す／`ws/install` を**手編集**して「直した」ことにする（次の build で消える）。
- **記録の無い走行**を根拠に docs・STATUS・Issue・memory を書く（[docs-first.md:42](docs-first.md)）。
- 走行記録と `run_manifest.v1` を**混同**する。契約・docs に無い語彙（`driver lease`・`operation_manager` 等）を**発明**する（[docs-first.md:32](docs-first.md)）。
- build スクリプトに `systemctl restart` / `enable` / `sudo` / `ssh` を足す（Deploy は別工程・§8）。
- `.github/workflows/ci.yml` に colcon job を **governance PR 無しで**足す（[parallel-workflow.md:177](parallel-workflow.md) §7.1 `.github/**` = governance 所有）。

## References

- 正本: [docs/jetson/03-build-deploy-run-and-run-records.md](../../docs/jetson/03-build-deploy-run-and-run-records.md)（§0 再ビルド判定 / §1 build-info / §2 rosdep・pip 例外 / §3-§6 run record・運用）
- [docs/setup/jetson-deploy.md](../../docs/setup/jetson-deploy.md) `:44` §2 タグ・`:54` bring-up 暫定 / `:60` §3 ビルド / `:77` §5 導入（enable/start しない）/ `:137` §8 更新・ロールバック
- [docs/jetson/02-remote-access-and-dev-link.md:231](../../docs/jetson/02-remote-access-and-dev-link.md)（§9 常時通電運用と `jetson` CLI）/ [docs/architecture/17-development-workflow.md:75-101](../../docs/architecture/17-development-workflow.md)（worktree ↔ clone）
- [docs/architecture/19-environments-and-config.md:115](../../docs/architecture/19-environments-and-config.md)（§6 git とリリース）/ [environments.md](environments.md)（dev/stg/prod = config・prod = タグ）
- [docs/architecture/20-dev-quality-and-testing.md:76](../../docs/architecture/20-dev-quality-and-testing.md)（colcon test = Phase 3）/ `pyproject.toml:33`（py310）/ [safety.md](safety.md)（実機ゲート・R-26）
- [docs/productization/09-run-manifest-and-plugin-composition.md:153-159](../../docs/productization/09-run-manifest-and-plugin-composition.md)（L3 `run_manifest.v1`＝別物）/ [layer-annotation.md](layer-annotation.md)
- [docs-first.md:42](docs-first.md)（引用は file:line）/ [parallel-workflow.md:46](parallel-workflow.md)（§1.1 完了ゲート）・[:177](parallel-workflow.md)（§7.1 所有）/ 用語は [docs/GLOSSARY.md](../../docs/GLOSSARY.md)「ビルド記録（build-info）」「走行記録（run record）」
