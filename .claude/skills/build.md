# Build & Test

ROS 2ワークスペースのビルドとテストを実行する。

## Steps
1. `colcon build --symlink-install` でビルド
2. `source install/setup.bash` で環境セットアップ
3. `colcon test` でテスト実行
4. `colcon test-result --verbose` で結果確認

## ボードでの build（Jetson `/opt/warehouse`）
- 素の `colcon build` は打たず `deploy/jetson/bin/build.sh`（記録付き・motion stack 稼働中は拒否・systemd unit を restart しない）。
- `colcon test` は [docs/architecture/20-dev-quality-and-testing.md:76](../../docs/architecture/20-dev-quality-and-testing.md) の Phase 3 まで任意。マージゲートはホストの `pytest tests/unit`。
- 規約は [.claude/rules/build-deploy-run.md](../rules/build-deploy-run.md)（Build ≠ Deploy ≠ Run・走行は `record-run.sh` で記録）。
