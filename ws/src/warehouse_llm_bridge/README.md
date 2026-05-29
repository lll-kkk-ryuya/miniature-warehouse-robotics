# warehouse_llm_bridge

- **ビルド**: ament_python
- **責務**: LLM Bridge Node（司令官サイクル・排他制御[HTTPキャンセル + gen_id]・キャラLLM）。Hermes Gateway 連携・Provider切替・Langfuse
- **主担当**: bridge / **Phase**: 0.5→3

> doc16 §9 により Gazebo/実機なしで偽トピック・偽 State Cache JSON で先行 E2E 検証可能に設計。
> rclpy と LLM を同一プロセスに同居させない（doc12）。
