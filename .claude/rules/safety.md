# 安全ルール

- 認証情報・APIキー・WiFiパスワードをコミットしない
- ロボット速度制限をコード内で強制する（ミニチュアスケールでは最大0.3 m/s）
- 実機デモ前に緊急停止ロジックをテストする
- Isaac Sim設定にクラウドGPU認証情報を含めない
- 安全機構の unit（R-26）は期待値を独立オラクルから取り、mutation で赤くなること（tautological / impl-coupled テスト禁止）＝[docs/architecture/20 §9](../../docs/architecture/20-dev-quality-and-testing.md)
