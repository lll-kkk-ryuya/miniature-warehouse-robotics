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
- トピック名: snake_case、ロボットごとにnamespace（例: /robot_1/cmd_vel）
- サービス名: snake_caseで動詞を含める（例: /assign_task）
- QoSプロファイルはデフォルトに頼らず明示的に設定する
- micro-ROSエージェントは再接続処理を必ず実装する
