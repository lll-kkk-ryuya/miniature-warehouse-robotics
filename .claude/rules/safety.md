# 安全ルール

- 認証情報・APIキー・WiFiパスワードをコミットしない
- ロボット速度制限をコード内で強制する（ミニチュアスケールでは最大0.3 m/s）
- 実機デモ前に緊急停止ロジックをテストする
- Isaac Sim設定にクラウドGPU認証情報を含めない
- 安全機構の unit（R-26）は期待値を独立オラクルから取り、mutation で赤くなること（tautological / impl-coupled テスト禁止）＝[docs/architecture/20 §9](../../docs/architecture/20-dev-quality-and-testing.md)
- 記録付き build / 走行記録（走行中 build 拒否・`ros2 param dump` の秘匿値 redaction）＝[build-deploy-run.md](build-deploy-run.md)（backlink・#656）。redaction の正本は `docs/jetson/03-build-deploy-run-and-run-records.md` §3
