---
paths:
  - "src/**/*.py"
  - "src/**/*.cpp"
  - "launch/**/*.py"
  - "config/**/*.yaml"
---

# ROS 2 開発ルール

- Pythonノードはrclpy、C++ノードはrclcppを使用する
- 全ノードでdeclare_parameter()によるパラメータ宣言を必須とする
- 状態管理が必要な場合はlifecycle nodeを使用する
- トピック名: snake_case、ロボットごとにnamespace（例: /bot1/cmd_vel）
- サービス名: snake_caseで動詞を含める（例: /assign_task）
- launch対象パスは `src/**/launch/**/*.py`（パッケージ内に launch を置く。トップレベル launch/ は使わない）
- QoSプロファイルはデフォルトに頼らず明示的に設定する
- micro-ROSエージェントは再接続処理を必ず実装する
