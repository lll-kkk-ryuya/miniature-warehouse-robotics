# tests/ — Python ユニット/安全テスト

ROS 2・実機なしで回る純ロジックテスト（doc16 §11、リスク R-26）。CI で毎push実行。

- `contracts.py` — 安全契約の参照実装（速度クランプ・known_locations・バッテリーポリシー）。実パッケージ実装後は本物の import に差し替える。
- `unit/test_safety_contracts.py` — 安全契約テスト（`@pytest.mark.safety`）。削除済み名称（`berth_charge_1`/`aisle_A` 等）が拒否され続けることの回帰ガードを含む。

実行:
```bash
pip install -e ".[dev]"   # or: pip install ruff pytest
pytest                    # 全テスト
pytest -m safety          # 安全系のみ
```
