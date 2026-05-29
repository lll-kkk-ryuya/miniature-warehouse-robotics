# warehouse_bringup

- **ビルド**: ament_python
- **責務**: launch・config の単一ソース（nav2/amcl/slam/twist_mux/footprint/速度上限）。`launch/` `config/` `rviz/`
- **主担当**: ros2 / **Phase**: 0.5

> doc16 §2/§5 準拠。パラメータの単一ソース。各ノードは config を持たず launch 引数で受け取る。
