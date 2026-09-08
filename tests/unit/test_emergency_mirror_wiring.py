"""Guardian estop → L2 mirror の **配線層** AST pin（#593 レビュー確定 follow-up ①②）。

`tests/unit/test_emergency_gate_sync.py` はミラー純ロジックと Policy Gate 統合
（`warehouse_mcp_server.emergency_sync`＝L2）を pin するが **`llm_bridge.py` を import
しない**ため、L4 node 側の購読・QoS・sweep timer 配線はどの変異でも suite green のまま
だった（#593 レビュー / mode-m1/05 §10・PR #594）。CI に colcon/ROS は無く rclpy import
も不可なので、`test_modec_noactuation.py` / `test_speed_band_bringup_wiring.py` の先例
どおり **ソースを AST で読む**（import 副作用ゼロ・host-runnable）。

仕様の出典（すべて実 Read・独立オラクル＝実装からの逆算ではなく docs/config 側の値）:

- docs/architecture/12-infrastructure-common.md:616 【2026-09-07 追補】② — set =
  `/bot{n}/cmd_vel/emergency` 購読（型 Twist・**購読側も RELIABLE を明示**＝QoS 非互換の
  silent no-match で fail-open にしない）/ clear = 無信号 > `emergency_clear_after_s` を
  **0.1s 周期 sweep** で検出 / 窓は `clear_after_from_config` の fail-closed 検証を通る。
- QoS の実体 = Guardian publisher
  ws/src/warehouse_safety/warehouse_safety/emergency_guardian.py:109-111,139-141
  （RELIABLE / KEEP_LAST / depth10。doc12 追補が正本として指す実装 anchor）。
- docs/architecture/15-mcp-platform.md:389-401 — twist_mux emergency 入力
  timeout 0.5 / priority 100（sweep 周期 0.1s + 窓 1.0s が必ずこの後に開く根拠）。
- config/warehouse.base.yaml:9-11 — fleet の正準 `robots:` リスト。`_BOTS` との突合は
  「config だけに居る bot は Guardian が estop しても L2 ミラーが聞いていない＝素通し
  （fail-open）」を CI で塞ぐ（#593 レビュー確定 follow-up ②）。
- .claude/rules/safety.md:7 — 独立オラクル + mutation で赤くなること（R-26）。

期待値はテスト側リテラル（topic 形・0.1s・QoS enum 名）。実装定数を import して
比較する tautology にはしない。
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
import yaml

pytestmark = [pytest.mark.unit, pytest.mark.safety]

_REPO_ROOT = Path(__file__).resolve().parents[2]
_LLM_BRIDGE_PY = _REPO_ROOT / "ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py"
_BASE_CONFIG = _REPO_ROOT / "config/warehouse.base.yaml"

# 仕様値（doc12:616 追補②）。実装から import せずリテラルで置く。
SPEC_TOPIC_TEMPLATE = "/{}/cmd_vel/emergency"
SPEC_SWEEP_PERIOD_S = 0.1


def _tree() -> ast.Module:
    return ast.parse(_LLM_BRIDGE_PY.read_text(encoding="utf-8"))


def _module_constant(tree: ast.Module, name: str) -> object:
    """モジュール定数を literal 評価で読む（Assign / AnnAssign どちらの形でも）。"""
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        if any(isinstance(t, ast.Name) and t.id == name for t in targets):
            return ast.literal_eval(value)
    raise AssertionError(f"モジュール定数 {name} が llm_bridge.py に無い")


def _fstring_parts(node: ast.expr) -> tuple[str, list[str]]:
    """f-string を（リテラル部を {} 連結した template, 差し込まれた Name のリスト）に潰す。"""
    assert isinstance(node, ast.JoinedStr), ast.dump(node)
    template: list[str] = []
    names: list[str] = []
    for part in node.values:
        if isinstance(part, ast.Constant):
            template.append(str(part.value))
        else:
            assert isinstance(part, ast.FormattedValue), ast.dump(part)
            assert isinstance(part.value, ast.Name), "topic の差し込みは単純な loop 変数のみ"
            template.append("{}")
            names.append(part.value.id)
    return "".join(template), names


def _is_emergency_subscription(node: ast.AST) -> bool:
    return (
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_subscription"
        and len(node.args) >= 2
        and isinstance(node.args[1], ast.JoinedStr)
        and any(
            isinstance(p, ast.Constant) and "cmd_vel/emergency" in str(p.value)
            for p in node.args[1].values
        )
    )


def _emergency_subscription_loop(tree: ast.Module) -> tuple[ast.For, ast.Call]:
    """`/cmd_vel/emergency` への create_subscription と、それを囲む for ループを返す。

    file 全体で当該購読はちょうど 1 箇所（「ループ外に個別 bot でもう 1 本」の変異も
    ここで落ちる）、かつ for ループ内でなければならない。
    """
    calls = [node for node in ast.walk(tree) if _is_emergency_subscription(node)]
    assert len(calls) == 1, (
        f"/cmd_vel/emergency 購読が file 内にちょうど 1 箇所でない: {len(calls)}"
    )
    loops = [
        loop
        for loop in ast.walk(tree)
        if isinstance(loop, ast.For) and any(node is calls[0] for node in ast.walk(loop))
    ]
    assert len(loops) == 1, "購読が単一の for ループ内に無い"
    return loops[0], calls[0]


# ─────────────── follow-up ②: _BOTS と config robots: の突合（fail-open 防止） ───────────────


def test_bots_tuple_matches_config_robots() -> None:
    """`_BOTS` ⊇ config `robots:` が崩れた bot は「Guardian は estop するのに L2 は通す」。

    fleet を config だけで増やすと、ミラー購読が無い新 bot の estop level 信号を
    L4 node が聞かず、`robot_in_emergency` は never-fire に戻る（fail-open）。逆方向
    （`_BOTS` だけの余剰 bot）は publisher 不在 topic への購読＝実害は無いが drift
    なので、完全一致で両方向とも CI で止める（doc12:616 追補② / base.yaml:9-11）。
    """
    bots = _module_constant(_tree(), "_BOTS")
    assert isinstance(bots, tuple)
    robots = yaml.safe_load(_BASE_CONFIG.read_text(encoding="utf-8"))["robots"]
    ids = [entry["id"] for entry in robots]
    assert len(ids) == len(set(ids)), f"config robots: に重複 id: {ids}"
    assert len(bots) == len(set(bots)), f"_BOTS に重複: {bots}"
    assert set(bots) == set(ids), (
        f"_BOTS {sorted(bots)} と config robots: {sorted(ids)} の不一致 — config だけに"
        " 居る bot は Guardian が estop しても L2 ミラーが購読していない（fail-open）"
    )


# ─────────────── follow-up ① (a): 購読が _BOTS 全 bot 分・正しい topic 形・型 ───────────────


def test_estop_subscription_iterates_bots_with_exact_topic() -> None:
    loop, call = _emergency_subscription_loop(_tree())
    # ループの iterable は _BOTS そのもの（別リテラルの複製だと config 突合が効かない）。
    assert isinstance(loop.iter, ast.Name) and loop.iter.id == "_BOTS", ast.dump(loop.iter)
    assert isinstance(loop.target, ast.Name)
    # topic は f"/{bot}/cmd_vel/emergency" ちょうど（typo・namespace 崩れ・接頭辞抜けで赤）。
    template, names = _fstring_parts(call.args[1])
    assert template == SPEC_TOPIC_TEMPLATE, f"topic 形が仕様と不一致: {template!r}"
    assert names == [loop.target.id], "topic に差し込まれるのが loop の bot 変数でない"
    # 型は Guardian publisher と同じ Twist（emergency_guardian.py:139-141）。
    assert isinstance(call.args[0], ast.Name) and call.args[0].id == "Twist"


def test_estop_callback_binds_loop_bot_per_subscription() -> None:
    """コールバックは `b=bot` の default 束縛で late-binding を回避している。

    `lambda _msg: mirror.on_stop_signal(bot, ...)` へ退行すると全購読が最後の bot に
    束縛され、bot1 の estop が bot2 として写像される（誤った bot を reject する
    fail-open/fail-closed 混合事故）。
    """
    loop, call = _emergency_subscription_loop(_tree())
    callback = call.args[2]
    assert isinstance(callback, ast.Lambda), ast.dump(callback)
    defaults = callback.args.defaults
    assert defaults, "lambda に default 束縛が無い（late-binding の罠）"
    bound = defaults[-1]
    assert isinstance(bound, ast.Name) and bound.id == loop.target.id, (
        "default に束縛されているのが loop の bot 変数でない"
    )
    bound_param = callback.args.args[-1].arg
    # 本体は on_stop_signal(<束縛 param>, ...) — loop 変数を直接読んでいたら赤。
    inner_calls = [
        node
        for node in ast.walk(callback.body)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "on_stop_signal"
    ]
    assert len(inner_calls) == 1
    first_arg = inner_calls[0].args[0]
    assert isinstance(first_arg, ast.Name) and first_arg.id == bound_param, (
        "on_stop_signal の bot 引数が default 束縛 param を使っていない"
    )
    loop_var_reads = [
        node
        for node in ast.walk(callback.body)
        if isinstance(node, ast.Name) and node.id == loop.target.id
    ]
    assert not loop_var_reads, "lambda 本体が loop 変数を直接読んでいる（late-binding）"


# ─────────────── follow-up ① (b): QoS RELIABLE の明示（silent no-match 防止） ───────────────


def test_estop_qos_is_explicit_reliable_keep_last_depth10() -> None:
    """購読 QoS は Guardian publisher と同じ RELIABLE を**明示**する（doc12:616 追補②）。

    rclpy の subscription 既定 QoS に任せる（または depth int だけ渡す）と、将来の
    既定変更や BEST_EFFORT 化で publisher と非互換になり、DDS は **エラー無しで
    no-match**＝ミラーが一切聞こえない fail-open になる。KEEP_LAST/depth10 は
    Guardian 側 reliable_qos（emergency_guardian.py:109-111）の鏡映で、モジュール
    冒頭コメントの「redelivery burst で clear が後ろへずれる（safe 方向）」の前提値。
    """
    tree = _tree()
    _, call = _emergency_subscription_loop(tree)
    qos_arg = call.args[3]
    assert isinstance(qos_arg, ast.Name), "QoS が名前付き QoSProfile でなく直値で渡っている"
    qos_calls = [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and any(isinstance(t, ast.Name) and t.id == qos_arg.id for t in node.targets)
        and isinstance(node.value, ast.Call)
    ]
    assert len(qos_calls) == 1, f"{qos_arg.id} の束縛が 1 箇所でない"
    qos = qos_calls[0]
    assert isinstance(qos.func, ast.Name) and qos.func.id == "QoSProfile"
    kwargs = {kw.arg: kw.value for kw in qos.keywords}
    reliability = kwargs.get("reliability")
    assert isinstance(reliability, ast.Attribute) and reliability.attr == "RELIABLE", (
        "reliability=RELIABLE の明示が無い（silent no-match で fail-open）"
    )
    history = kwargs.get("history")
    assert isinstance(history, ast.Attribute) and history.attr == "KEEP_LAST"
    depth = kwargs.get("depth")
    assert isinstance(depth, ast.Constant) and depth.value == 10


# ─────────────── follow-up ① (c): 0.1s 周期 sweep timer（clear 経路の生存） ───────────────


def test_sweep_timer_wired_at_documented_cadence() -> None:
    """`create_timer(EMERGENCY_SWEEP_PERIOD_S, self._sweep_emergency_mirror)` が存在する。

    timer が消えると clear 経路が死に、一度 estop した bot は恒久 reject（safe 方向
    だが復帰不能）。周期 0.1s は doc12:616 追補②「0.1s 周期 sweep」の仕様値。
    """
    tree = _tree()
    assert _module_constant(tree, "EMERGENCY_SWEEP_PERIOD_S") == SPEC_SWEEP_PERIOD_S
    timers = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "create_timer"
        and len(node.args) >= 2
        and isinstance(node.args[0], ast.Name)
        and node.args[0].id == "EMERGENCY_SWEEP_PERIOD_S"
        and isinstance(node.args[1], ast.Attribute)
        and node.args[1].attr == "_sweep_emergency_mirror"
    ]
    assert len(timers) == 1, "0.1s sweep timer の配線が無い/重複している"
    # timer callback の実体が mirror.sweep を呼ぶ（空メソッド化の変異で赤）。
    sweeps = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef) and node.name == "_sweep_emergency_mirror"
    ]
    assert len(sweeps) == 1
    body_calls = [
        node.func.attr
        for node in ast.walk(sweeps[0])
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    ]
    assert "sweep" in body_calls, "_sweep_emergency_mirror が mirror.sweep を呼んでいない"


# ─────────────── follow-up ① (d): config 検証済み clear 窓がミラーへ渡る ───────────────


def test_mirror_receives_config_resolved_clear_window() -> None:
    """`EmergencyLevelMirror(<...policy_gate.set_emergency>, clear_after_from_config(cfg))`。

    第 2 引数を落とすと常に既定 1.0s になり、config で窓を締めた運用
    （`policy_gate.emergency_clear_after_s`）が黙って無視される。fail-closed 検証
    （非数値・floor 未満の起動拒否＝doc12:616 追補②）も素通りになる。純ロジック側の
    `test_configured_window_shifts_the_boundary` はコンストラクタ配線までは見ていない。
    """
    tree = _tree()
    ctors = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "EmergencyLevelMirror"
    ]
    assert len(ctors) == 1, "EmergencyLevelMirror の構築が 1 箇所でない"
    ctor = ctors[0]
    assert len(ctor.args) == 2, "clear 窓引数が渡っていない（既定へ silent fallback）"
    setter = ctor.args[0]
    assert isinstance(setter, ast.Attribute) and setter.attr == "set_emergency"
    assert isinstance(setter.value, ast.Attribute) and setter.value.attr == "policy_gate", (
        "setter が tools.policy_gate 経由でない（別 gate へ feed すると dispatch 側と分裂）"
    )
    window = ctor.args[1]
    assert isinstance(window, ast.Call), "clear 窓が config 解決を経ていない"
    assert isinstance(window.func, ast.Name) and window.func.id == "clear_after_from_config"
    assert window.args and isinstance(window.args[0], ast.Name) and window.args[0].id == "cfg"
