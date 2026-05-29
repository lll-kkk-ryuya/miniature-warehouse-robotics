# warehouse_interfaces

- **ビルド**: ament_python（Phase 0.5〜3）。Phase 4 で構造化 `.msg` 導入時に ament_cmake へ移行
- **責務**: 凍結契約のコード化 — pydantic schemas（Situation/Command/proposal）・`StateStore`/`GenStore` IF・共有パス定数。初期は `.msg/.srv` を持たない純 Python 契約パッケージ
- **主担当**: ros2/bridge / **Phase**: 0.5

> doc16 §2/§3 準拠。初期は `std_msgs/String`(JSON) 運用のため ament_python。`.msg` 化（Phase 4）の際に ament_cmake へ移行（rosidl は Python パッケージで生成不可）。
