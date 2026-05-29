# sim/ — シミュレーション環境

実機到着前にソフトウェアの95%を検証するための環境（`docs/architecture/06-implementation-phases.md` Phase 0.5 / Phase 5）。

| ディレクトリ | 内容 | 実行環境 | 担当agent |
|------------|------|---------|----------|
| `gazebo/` | 仮想ジオラマ(SDF)・minicar URDF・worlds・Nav2チューニング | Mac M4 上の Docker (Gazebo Harmonic) | sim-specialist |
| `isaac/` | Isaac Sim 5.1 デジタルツインシーン・ROS 2 Bridge設定 | RunPod A10G (クラウドGPU) | sim-specialist |

> ⚠️ クラウドGPU認証情報は含めない（`.claude/rules/safety.md`）。
