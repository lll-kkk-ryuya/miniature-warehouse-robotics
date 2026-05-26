# Build & Test

ROS 2ワークスペースのビルドとテストを実行する。

## Steps
1. `colcon build --symlink-install` でビルド
2. `source install/setup.bash` で環境セットアップ
3. `colcon test` でテスト実行
4. `colcon test-result --verbose` で結果確認
