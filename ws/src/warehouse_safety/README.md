# warehouse_safety

- **ビルド**: ament_python
- **責務**: Emergency Guardian（50ms周期目標、LLM非経由）+ twist_mux 設定（Emergency=100 / Nav2=10）
- **主担当**: bridge / **Phase**: 0.5

> doc16 §11 によりユニットテスト必須（距離・バッテリー・stale・blocked の拒否ケースを偽入力で検証）。
> 周期保証は非RTでベストエフォート。最終防衛線は ESP32(Layer 0)。
