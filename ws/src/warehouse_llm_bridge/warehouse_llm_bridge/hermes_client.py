"""HermesClient — the single commander LLM client over the Hermes Gateway.

The bridge sends the situation to ``{base_url}/v1/chat/completions`` (OpenAI
Chat-Completions compatible, doc13 §5.1 / doc15:30-44) and gets back the
commander's decision as a JSON ``Command`` in the assistant message content. The
provider (Claude / ChatGPT / Gemini / Grok) is chosen server-side by Hermes'
``active_provider`` config — the bridge always sends ``model: "hermes-agent"``
and never changes per request (doc13:171,402-421). This is the Phase-4 4-way
comparison mechanism.

Trace ownership (doc08:354-356 / doc13:517, Pattern A): the call goes through
``from langfuse.openai import AsyncOpenAI`` so the generation is captured under the
Bridge-owned trace established by the :class:`~warehouse_llm_bridge.tracing.Tracer`
(``scheduler`` opens the per-turn trace around ``decide``). The langfuse + openai
SDKs are imported **lazily** (pip extras, not pytest/ruff deps) and langfuse is
fail-open; the pure parser :func:`parse_command_content` needs neither and is
unit-tested directly.

Transport notes:
* **stateless** chat/completions — no ``run_id`` / ``/v1/runs/{id}/stop`` on the
  adopted path (doc13:396-436). Cancellation is the caller's ``asyncio.wait_for``
  (Layer A client-side); the explicit run ``/stop`` is dropped — in-process
  dispatch has no server-side run to stop (Issue #54 resolved, doc08:173-179).

Failure contract (consumed by the scheduler, doc08:288-293): a transport / non-2xx
error raises :class:`LLMUnavailableError` (→ Nav2-only); a malformed body raises
``ValueError`` (→ ignore this cycle).
"""

import json
import logging
from collections.abc import Mapping
from typing import Any

from eval_sdk.seed import seed_for

from warehouse_llm_bridge.llm_client import LLMClient, LLMUnavailableError

log = logging.getLogger(__name__)

# Sent as ``model`` on every request; Hermes routes to its active_provider
# (doc13:171 — NOT the provider's own model id).
HERMES_MODEL = "hermes-agent"

# ── Langfuse-owner selector (Pattern A vs Option D; doc13:517) ───────────────
# WHO mints the Langfuse trace + LLM generation for the commander cycle:
#
# * ``"bridge"`` (DEFAULT, Pattern A, doc13:517 / doc08:354-356): the BRIDGE owns the
#   trace. ``decide`` calls through ``from langfuse.openai import AsyncOpenAI`` so the
#   generation is captured under the Bridge-owned trace (opened by ``LangfuseTracer.turn``)
#   and ``langfuse_prompt=`` links the managed prompt. The Hermes-side Langfuse plugin is
#   DISABLED on this path to avoid double-counting (doc13:517). This is the frozen, shipped
#   behaviour and stays the default — UNCHANGED.
# * ``"hermes_plugin"`` (Option D, OPT-IN, CONTINGENT on the live audio D-verify PASS): the
#   Hermes Langfuse plugin is left ON and mints the root trace + generation server-side from a
#   seed it derives from the request session/task ids (plugin __init__:544 =
#   ``create_trace_id(seed=f"{session_id or 'sessionless'}::{task_id or task_key}")``). To make
#   the scorer (#6) re-derive that id with zero data coupling, the Bridge pins the session id to
#   ``H = seed_for(run_id, gen_id)`` via the ``X-Hermes-Session-Id`` header; on the stateless
#   chat path the plugin defaults task_id to session_id so the plugin seed collapses to
#   ``f"{H}::{H}"`` (eval_sdk.seed.plugin_seed / derive_plugin_trace_id). On THIS path ``decide``
#   uses plain ``from openai import AsyncOpenAI`` (NO langfuse.openai wrapper, so the Bridge does
#   NOT create a duplicate generation) and DROPS ``langfuse_prompt=`` (the plugin has no
#   ``prompt=`` path — managed-prompt link is lost; see spike/langfuse-plugin-d/
#   MANAGED-PROMPT-DECISION.md). Drift-detect: the Bridge reads the echoed session id from the
#   ``X-Hermes-Session-Id`` RESPONSE HEADER and, if it != H, LOGS that this cycle's score join
#   WOULD ORPHAN (the plugin seeded on a different id than the scorer re-derives). It does NOT
#   suppress the scorer (a separate process, score_send.py) — there is no cross-lane suppression
#   path; the Bridge only notes it. FAIL-OPEN: it NEVER raises into the cycle (R-26).
# CROSS-LANE STRING CONTRACT: these two literals MUST stay byte-identical to the scorer's
# copies in warehouse_orchestrator.score_send (_LANGFUSE_OWNER_BRIDGE / _LANGFUSE_OWNER_HERMES_PLUGIN).
# We do NOT import across packages (one-way dependency: each lane depends only on
# warehouse_interfaces + eval_sdk, parallel-workflow §2.1), so the two copies are mirrored by
# value. ONE knob (WAREHOUSE_LANGFUSE_OWNER) reads the SAME values on both legs, so a rename on
# only one side would silently orphan every score onto the wrong trace recipe. A cross-check unit
# (tests/unit/test_hermes_client_option_d.py) asserts the two copies are equal WITHOUT a runtime
# cross-lane import.
LANGFUSE_OWNER_BRIDGE = "bridge"
LANGFUSE_OWNER_HERMES_PLUGIN = "hermes_plugin"

# Header the Bridge sends to pin the plugin's session_id to H, and (on the /v1/chat/completions
# path) the header the gateway echoes it back in — the plugin seeds its trace from it (see
# spike/langfuse-plugin-d/FORK-DISTINCTION.md and the er-audio-fork
# hlf-g0-langfuse/PLUGIN-TRACEID-ANALYSIS.md). NOTE: the body ``session_id`` field
# (api_server.py:1515 ``effective_session_id = result.get("session_id")``) is the echo for the
# SEPARATE ``/api/sessions/.../chat`` endpoint, NOT /v1 — so the /v1 drift-detect reads this
# RESPONSE HEADER, never the body (see :meth:`_detect_session_drift`). Header name is the canonical
# Hermes session id header; the value is OUR deterministic join key H (NOT a Hermes-internal id).
HERMES_SESSION_HEADER = "X-Hermes-Session-Id"

# Env var that selects the Langfuse owner (env over config, like the other Bridge run-level
# labels in llm_bridge.py — provider/scenario/run_id). default = bridge (Pattern A UNCHANGED).
LANGFUSE_OWNER_ENV = "WAREHOUSE_LANGFUSE_OWNER"
_LANGFUSE_OWNERS = frozenset({LANGFUSE_OWNER_BRIDGE, LANGFUSE_OWNER_HERMES_PLUGIN})


def resolve_langfuse_owner(cfg: Mapping[str, object], env: Mapping[str, str] | None = None) -> str:
    """Resolve who owns the Langfuse trace: ``WAREHOUSE_LANGFUSE_OWNER`` env, then ``hermes.langfuse_owner`` config, else default ``bridge``.

    Pattern A (``bridge``) is the DEFAULT and stays the shipped behaviour; ``hermes_plugin``
    (Option D) is opt-in and CONTINGENT on the live audio D-verify passing. An unknown value
    fails SAFE to ``bridge`` (never silently enables Option D) and is logged. Pure (env injected
    for tests); never raises on a malformed config block. Uses ``isinstance(..., Mapping)`` to
    match the scorer mirror (``score_send.resolve_pattern_d``) byte-for-byte so the two lanes
    cannot diverge on a non-dict Mapping config.
    """
    import os

    env = os.environ if env is None else env
    raw = env.get(LANGFUSE_OWNER_ENV)
    if raw is None or not str(raw).strip():
        hermes = cfg.get("hermes") if isinstance(cfg, Mapping) else None
        raw = hermes.get("langfuse_owner") if isinstance(hermes, Mapping) else None
    owner = str(raw).strip() if raw is not None else ""
    if owner in _LANGFUSE_OWNERS:
        return owner
    if owner:
        log.warning(
            "unknown %s=%r; falling back to %r (Pattern A, Option D stays off)",
            LANGFUSE_OWNER_ENV,
            owner,
            LANGFUSE_OWNER_BRIDGE,
        )
    return LANGFUSE_OWNER_BRIDGE


# Mode-neutral base system prompt: the output contract (frozen Command JSON,
# doc mode-a/08a:257-264), the safety-over-efficiency / 3-stage battery guidance common to
# every mode (08a:243-250, the three tiers 10 / 10-20 / 20-30 % per 08a:246-249) and the
# gen_id B-3 note (08a:253). It carries NO traffic / robot-
# selection specifics — those are mode-specific. Mode A/B additions (the commander
# assigns BOTH bots itself: task allocation + deadlock rules) live in MODE_A_RULES; Mode
# C delegates robot selection to the Open-RMF allocator (doc08c:154 「robot 指定なし」), so
# this base must NOT instruct per-bot allocation. Mode C does NOT use this base at all —
# it gets the standalone :data:`MODE_C_PROMPT` (doc08c:138-180: strategic-only role,
# 3-stage battery, traffic.escalation gate + escalation.id, action 制限 navigate|stop|
# charge), which is faithful to doc08c and deliberately diverges from this base on the
# action set and the collision-avoidance role (see MODE_C_PROMPT + build_system_prompt).
# The gen_id line is advisory: the LLM emits a Command (no gen_id
# field) and action_map injects the real gen_id + idempotency_key (model-b, #41/#54).
SYSTEM_PROMPT = (
    "あなたは倉庫ロボット2台の司令官AIです。状況JSONを読み、安全性を効率性より優先して"
    "（衝突回避を最優先に）2台分の指示を決定してください。\n"
    "バッテリー方針（3段階）: 10%以下は緊急停止（Policy Gate が全コマンド拒否、Emergency "
    "Guardian が自動停止）、10-20%は新規タスク割当禁止・充電ステーションへの移動を推奨、"
    "20-30%は次タスク割当禁止・充電候補として検討。\n"
    "状況JSON の gen_id は B-3 安全機構（15-mcp-platform.md §2）。指示には Bridge が自動付与する"
    "ので、常に最新の状況JSONにのみ基づいて判断してください。\n"
    "キャラLLM交渉を発動する場合のみ、応答の最上位に start_negotiation オブジェクトを足してください"
    "（発動条件と使い方は各モードの指示を参照。通常は省略）。\n"
    "必ず次のJSON形式のみで返答してください（前後に文章を付けない）:\n"
    '{"reasoning": "判断理由", "commands": [{"bot": "bot1", "action": '
    '"navigate|wait|stop|yield|charge", "destination": "場所名", "duration": 秒数, '
    '"via": "経由ルート", "retreat_to": "退避先"}], '
    '"start_negotiation": {"starter": "bot1", "deadlock_or_escalation_id": "<id>"}, '
    '"priority_explanation": "優先順位の説明"}'
)

# Traffic modes where the commander LLM manages traffic AND robot selection itself
# (Mode A/B). Mode C (open-rmf) delegates both to Open-RMF, so it gets NEITHER the
# per-bot task allocation NOR the deadlock rules below (doc14:163-164 / doc08c:154) —
# it gets the standalone MODE_C_PROMPT instead.
# Mirrors llm_bridge.NAV2_BRIDGE_MODES (none/simple = Mode A/B).
MODE_A_TRAFFIC_MODES = frozenset({"none", "simple"})

# Mode A/B additions appended to the base prompt for Mode A/B ONLY: (1) per-bot task
# allocation from pending_tasks — Mode A's commander assigns BOTH bots (doc08a output
# names bot1/bot2:261), unlike Mode C where the allocator picks the bot (doc08c:154); and
# (2) deadlock detection + resolution (doc mode-a/08a:316-334 「システムプロンプトへの追加
# 指示」). The 0.4m / 2.5rad thresholds are (b) docs-ILLUSTRATIVE values (doc08a:278-279;
# NOT in config/safety, NOT a frozen contract) reproduced from the doc text. The 200mm
# no-passing aisle context reaches the LLM via situation.warehouse.layout; retreat_to
# uses the config location keys retreat_A / retreat_B (doc08a:387). Mode C omits this
# block entirely — there Open-RMF owns traffic + robot selection (doc14:163-164 / 08c).
MODE_A_RULES = (
    "\n\n## タスク割当（Mode A）\n"
    "状況JSON の pending_tasks にあるタスクを、手が空いている（status が idle で current_task が"
    "null の）ロボットに navigate で割り当ててください（行先は task の to）。\n"
    "\n## デッドロック検出ルール\n"
    "以下の条件が全て満たされたらデッドロックと判断:\n"
    "1. 2台とも「停止中だがゴール保持」: status が idle（velocity≈0）かつ current_task が null"
    'でない（State Cache は status に "blocked" を出さず moving/idle のみ。idle かつ'
    " current_task!=null ＝ 進むべきなのに止まっている。history に同一 navigate が連続ターン"
    "残れば持続の裏付け）\n"
    "2. 2台の距離が 0.4m 以内（各 robot の position から算出）\n"
    "3. 2台の heading が対向（heading 差が 2.5rad 以上）\n"
    "デッドロック検出時の解消手順:\n"
    "1. 優先度が低い方のタスクのロボットに yield を指示（retreat_to は最寄りの退避先"
    "＝ retreat_A / retreat_B）\n"
    "2. 優先度が高い方のロボットに wait を指示（duration=5秒）\n"
    "3. 優先度が同じ場合: 先着（task_id が小さい方）を優先\n"
    "predicted_position_3s が同一地点に収束している場合は、デッドロック予兆として事前に回避"
    "（wait または via で迂回）。ただし双方が停止中だと予測位置は各自の現在地に縮退し収束"
    "しないため、静止デッドロックは上記条件1〜3で、接近中の衝突予兆は predicted_position_3s で検出する。"
    "\n## キャラLLM交渉（任意・演出）\n"
    "- デッドロックを検出したら、自分で解消する前に bot1/bot2 のキャラLLM交渉を発動できます"
    "（任意の演出。doc14 / 08a:254）。発動するには応答の最上位に start_negotiation を入れます: "
    '"start_negotiation": {"starter": "<先手 bot1|bot2>", "deadlock_or_escalation_id": '
    '"<検出したデッドロックの識別子>"}。commands と同時に出してよい（同サイクルで発動）。\n'
    "- 状況JSON に negotiation_proposal があれば、その合意（agreed_action）を検証し、安全条件"
    "（バッテリー / 距離 / Emergency中でないこと）を満たすなら採用してコマンドに反映してください"
    "（稟議制＝最終判断はあなた。doc08a:255 / doc14:157）。agreed_action.to は表示名なので、"
    "retreat_to / destination には対応する場所キー（retreat_A / retreat_B 等）に解決して載せてください"
    "（doc08a:387）。安全条件に反するなら採用せず独自判断してください。"
)


# Mode C (open-rmf) standalone system prompt, faithful to doc08c:138-180. Mode C is a
# STANDALONE prompt — NOT base + a rules block (the Mode A pattern) — because the base
# conflicts with Mode C on two DESIGN axes: the action set (base navigate|wait|stop|yield|
# charge vs Mode C navigate|stop|charge, doc08c:136,176) and the role (base / Mode A say
# 衝突回避を最優先 per doc08a:250 vs Mode C delegates collision avoidance to Open-RMF,
# doc08c:159). Appending a rules block to the base would yield a self-contradictory prompt,
# so Mode C replaces it wholesale. (Battery is NOT a Mode-A-vs-Mode-C divergence: the base
# (08a:246-249), Mode A and Mode C (doc08c:155-158) are ALL 3-stage — the base's earlier
# 2-stage drift was reconciled to 08a:246-249. So battery does NOT motivate the standalone
# split; the action set and the collision-avoidance role do.)
#
# Provenance: this content is (b) docs-ILLUSTRATIVE (doc08c:141-179 prompt example), NOT a
# frozen contract. The action restriction navigate|stop|charge is a STRICT SUBSET of the
# frozen CommandAction enum (warehouse_interfaces.schemas:135-140) — the prompt narrows
# USAGE only; the parser / Command schema are NOT narrowed (Mode A's wait/yield must still
# validate). The 3-stage battery thresholds (10 / 10-20 / 20-30 %) are reproduced from
# doc08c:155-158 — the range values are verbatim, only minor JP spacing is normalized (not
# invented; not in config/safety). doc08c:147-151's hard-coded
# layout coordinates are intentionally NOT copied here: KNOWN_LOCATIONS / config is the
# canonical source and the layout reaches the LLM via situation.warehouse.layout (as in
# Mode A), so reproducing the illustrative coords would risk drift. The output JSON keys
# match the frozen Command / CommandItem shape (schemas.py:143-175); only bot/action/
# destination are used (the other CommandItem fields are optional). start_negotiation /
# negotiation_proposal are forward-references to the character-LLM negotiation (doc08c:
# 164-165, Mode C climax, marked optional) and stay advisory.
MODE_C_PROMPT = (
    "あなたは倉庫ロボット2台の戦略司令官AIです。状況JSONを読み、戦略判断"
    "（タスク割当・優先順位・バッテリー管理）を行ってください。\n"
    "\n## あなたの役割\n"
    "タスク割当・優先順位変更・バッテリー管理のみを行います。経路選択・衝突回避・待機指示は"
    "交通管理システム（Open-RMF）が自動処理するため、あなたは関与しません。\n"
    "倉庫レイアウトと場所名は状況JSON（warehouse.layout・各ロボット/タスクの場所名）を参照して"
    "ください。\n"
    "\n## ルール\n"
    "- 未処理タスクを割り当てる（pickup/dropoff と優先度を指定）。ロボットの選択はアロケーターに"
    "任せる＝robot 指定なし（デバッグ時のみ robot 指定可）。\n"
    "- バッテリー管理（3段階）:\n"
    "  - 10%以下: 緊急停止（Policy Gate が全コマンド拒否、Emergency Guardian が自動停止）\n"
    "  - 10-20%: 新規タスク割当禁止、充電ステーションへの移動を推奨\n"
    "  - 20-30%: 次タスク割当禁止、充電候補として検討\n"
    "- 交通管理（衝突回避・経路選択・待機）には関与しない — Open-RMF が自動処理する。\n"
    "- 状況JSON の traffic.escalation フィールドが null でない場合のみ、Open-RMF が解決できなかった"
    "問題に対処する（suggested_action は助言ヒント＝タスク再割当・目的地変更等の戦略ツールへ写す。"
    "応答に id が要るツールには traffic.escalation.id を渡す。経路・待機には関与しない）。\n"
    "\n## 安全機構（必ず守る）\n"
    "- 状況JSON の gen_id フィールドを、すべての MCP tool 呼出しの gen_id 引数にそのまま渡して"
    "ください（B-3 安全機構、15-mcp-platform.md §2）。\n"
    "- traffic.escalation が立っている（非null）ときは、応答の最上位に start_negotiation を入れて"
    "キャラLLM交渉を発動できます（Mode C ではクライマックス演出用、任意）: "
    '"start_negotiation": {"starter": "bot1", "deadlock_or_escalation_id": '
    '"<traffic.escalation.id>"}。\n'
    "- negotiation_proposal が状況JSONに含まれていれば、その提案を検証し、安全条件を満たすなら"
    "採用してください。\n"
    "\n## 使用可能なアクション\n"
    "- navigate: 目的地を指定（経路は Open-RMF が決定）\n"
    "- stop: 緊急停止\n"
    "- charge: 充電ステーションへ移動\n"
    "\n必ず次のJSON形式のみで返答してください（前後に文章を付けない・start_negotiation は発動時のみ）:\n"
    '{"reasoning": "判断理由を日本語で説明", "commands": [{"bot": "bot1", "action": '
    '"navigate|stop|charge", "destination": "場所名"}], '
    '"start_negotiation": {"starter": "bot1", "deadlock_or_escalation_id": "<id>"}, '
    '"priority_explanation": "判断の優先順位の説明"}'
)


def build_system_prompt(mode: str) -> str:
    """Return the commander system prompt for the given ``traffic_mode``.

    Mode A/B (``none``/``simple``) get the base prompt PLUS :data:`MODE_A_RULES`
    (per-bot task allocation + deadlock detection / yield resolution, doc mode-a/08a:
    316-334), since the commander manages traffic and robot selection itself. Mode C
    (``open-rmf``) gets the standalone :data:`MODE_C_PROMPT` (doc08c:138-180): a
    strategic-only commander that delegates route / collision / wait to Open-RMF (doc14:164
    / doc08c:159) AND robot selection to the allocator (doc08c:154), with 3-stage battery, a
    ``traffic.escalation`` gate (+ ``escalation.id``) and the restricted action set
    ``navigate|stop|charge``. The base is unused in Mode C (it would contradict on the action
    set and the collision-avoidance role — see MODE_C_PROMPT). Pure (no ROS / network) so the
    mode-awareness is unit-testable.
    """
    if mode in MODE_A_TRAFFIC_MODES:
        return SYSTEM_PROMPT + MODE_A_RULES
    return MODE_C_PROMPT


# CommandItem location string fields. The frozen ``CommandItem._known_location``
# validator (warehouse_interfaces.schemas) rejects any non-None value not in
# KNOWN_LOCATIONS, so an empty string ("") fails and the scheduler silently drops the
# WHOLE cycle (doc08:293). Some models (notably gemini-2.5-flash) emit e.g.
# ``retreat_to: ""`` on a plain navigate. An empty string carries no location meaning,
# so the bridge normalizes "" -> None (the schema's absent value) BEFORE validation.
# This keeps the FROZEN contract strict for a real unknown location — only the bridge
# normalizes its own LLM input (#88 live finding).
_LOCATION_FIELDS = ("destination", "via", "retreat_to")


def _coerce_empty_locations(command: dict) -> None:
    """In-place: coerce blank ("") location fields on each command item to None (#88)."""
    items = command.get("commands")
    if not isinstance(items, list):
        return
    for item in items:
        if not isinstance(item, dict):
            continue
        for field in _LOCATION_FIELDS:
            if item.get(field) == "":
                item[field] = None


def parse_command_content(content: object) -> dict:
    """Parse the assistant message *content* (a JSON string) into a Command dict.

    Raises ``ValueError`` for non-text / non-JSON / non-object content so the
    scheduler treats it as an invalid response and ignores the cycle (doc08:293)
    rather than dispatching garbage. Empty-string location fields are normalized to
    ``None`` (:func:`_coerce_empty_locations`, #88) so a model's ``retreat_to:""`` does
    not fail the frozen validator and silently drop the cycle.
    """
    if not isinstance(content, str):
        raise ValueError(f"message content is not text: {type(content).__name__}")
    try:
        command = json.loads(content)
    except json.JSONDecodeError as exc:
        raise ValueError(f"command content is not valid JSON: {exc}") from exc
    if not isinstance(command, dict):
        raise ValueError(f"command JSON is not an object: {type(command).__name__}")
    _coerce_empty_locations(command)
    return command


def parse_command(response: dict[str, Any]) -> dict:
    """Extract the Command dict from a raw chat-completion *response dict*.

    The dict form (e.g. an httpx ``.json()`` or a recorded fixture);
    :meth:`HermesClient.decide` uses :func:`parse_command_content` directly on the
    SDK object's ``message.content``.
    """
    try:
        content = response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ValueError(f"unexpected chat-completion shape: {exc}") from exc
    return parse_command_content(content)


class HermesClient(LLMClient):
    """Send the situation to the Hermes Gateway and return the commander Command."""

    def __init__(
        self,
        base_url: str,
        *,
        api_key: str = "",
        system_prompt: str = SYSTEM_PROMPT,
        model: str = HERMES_MODEL,
        timeout: float = 5.0,
        langfuse_prompt: object | None = None,
        langfuse_owner: str = LANGFUSE_OWNER_BRIDGE,
        run_id: str | None = None,
    ) -> None:
        """Wire the endpoint.

        ``timeout`` is the SDK transport ceiling (doc13 sample 5.0s); the active
        per-cycle bound is the scheduler's ``asyncio.wait_for(2.5s)`` (doc08:140),
        which cancels the request first under normal slowness (Layer A).

        ``langfuse_prompt`` is the optional Langfuse Prompt-Management object (from
        :func:`warehouse_llm_bridge.prompts.resolve_commander_prompt`); when present it is
        passed as ``langfuse_prompt=`` to link each generation to the managed prompt version
        for prompt-level analytics (Pattern A, doc08 §Langfuse Prompt Management 方針).
        ``None`` (the default, and when the code fallback is used) leaves the call unchanged.

        ``langfuse_owner`` selects who mints the Langfuse trace/generation
        (:data:`LANGFUSE_OWNER_BRIDGE` default = Pattern A, UNCHANGED;
        :data:`LANGFUSE_OWNER_HERMES_PLUGIN` = Option D, opt-in). On the Option-D path the
        Bridge sends ``X-Hermes-Session-Id: H`` with ``H = seed_for(run_id, gen_id)`` so the
        Hermes Langfuse plugin seeds its trace from our deterministic join key, and DROPS the
        ``langfuse.openai`` wrapper + ``langfuse_prompt=`` (see the module docstring on
        ``LANGFUSE_OWNER_*``). ``run_id`` is the shared ``WAREHOUSE_RUN_ID``-derived run id
        (:func:`eval_sdk.seed.resolve_run_id`); it is REQUIRED for a usable Option-D join (a
        blank/None run_id makes H non-joinable, so the path fails open to "no score this cycle"
        per the drift-detect contract). Unused on the default Pattern-A path.
        """
        # OpenAI SDK appends ``/chat/completions`` to ``base_url`` itself.
        self._base_url = base_url.rstrip("/") + "/v1"
        self._api_key = api_key
        self._system_prompt = system_prompt
        self._model = model
        self._timeout = timeout
        self._langfuse_prompt = langfuse_prompt
        self._langfuse_owner = langfuse_owner
        self._run_id = run_id

    async def decide(self, situation: dict) -> dict:
        """Call Hermes (traced) and return the parsed Command JSON dict.

        Raises :class:`LLMUnavailableError` on a transport / non-2xx error,
        ``ValueError`` on a malformed response body (doc08:288-293).

        On the default :data:`LANGFUSE_OWNER_BRIDGE` path this is UNCHANGED Pattern A:
        the ``langfuse.openai`` wrapper captures the generation under the Bridge-owned
        trace and ``langfuse_prompt=`` links the managed prompt. On the opt-in
        :data:`LANGFUSE_OWNER_HERMES_PLUGIN` (Option D) path it instead sends the
        ``X-Hermes-Session-Id: H`` header, uses a plain (un-wrapped) ``AsyncOpenAI`` and
        drift-detects the echoed session id — see :meth:`_decide_plugin_owned`.

        The R-26 cycle contract is IDENTICAL on both paths: a transport/2xx failure →
        ``LLMUnavailableError`` (→ Nav2-only); a malformed body → ``ValueError`` (→ ignore
        cycle); the parsed Command is the only return. Observability is obs-only and never
        actuates; the drift-detect only suppresses an obs join and NEVER raises into the cycle.
        """
        if self._langfuse_owner == LANGFUSE_OWNER_HERMES_PLUGIN:
            return await self._decide_plugin_owned(situation)
        return await self._decide_bridge_owned(situation)

    def _build_create_kwargs(self, situation: dict) -> dict[str, Any]:
        """Assemble the shared ``chat.completions.create`` kwargs (both owner paths).

        Identical request body on both paths so the commander prompt + situation the LLM
        sees is byte-for-byte the same regardless of who owns the trace (R-26: the
        decision logic must not change with the observability owner).
        """
        return {
            "model": self._model,
            "messages": [
                {"role": "system", "content": self._system_prompt},
                {"role": "user", "content": json.dumps(situation)},
            ],
            "timeout": self._timeout,
        }

    async def _decide_bridge_owned(self, situation: dict) -> dict:
        """Pattern A (DEFAULT, doc13:517): Bridge-owned trace via ``langfuse.openai``.

        Behaviour-preserving — byte-for-byte the historical ``decide`` body.
        """
        # Lazy: langfuse.openai is a pip extra and traces the generation under the
        # active Bridge-owned trace (tracing.LangfuseTracer.turn); openai supplies
        # the error types. Neither is needed by tests (they use a fake client). A
        # missing extra degrades to Nav2-only (LLMUnavailableError) rather than
        # crashing the commander cycle (doc08:288-292 fallback).
        try:
            import openai
            from langfuse.openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMUnavailableError(f"langfuse/openai not installed: {exc}") from exc

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key or "no-key")
        create_kwargs = self._build_create_kwargs(situation)
        # Link the generation to the managed prompt version (doc08 §Langfuse Prompt
        # Management 方針). The langfuse.openai wrapper supports langfuse_prompt= on
        # .create(); only added when a real prompt object is present so the default path
        # (code fallback / no langfuse) is byte-for-byte unchanged.
        if self._langfuse_prompt is not None:
            create_kwargs["langfuse_prompt"] = self._langfuse_prompt
        try:
            completion = await client.chat.completions.create(**create_kwargs)
        except openai.OpenAIError as exc:
            raise LLMUnavailableError(f"hermes request failed: {exc}") from exc
        try:
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(f"unexpected completion shape: {exc}") from exc
        return parse_command_content(content)

    async def _decide_plugin_owned(self, situation: dict) -> dict:
        """Option D (OPT-IN): Hermes Langfuse plugin owns the trace; Bridge pins the seed.

        Differences vs Pattern A (and ONLY these — the request body, the failure contract
        and the returned Command are identical):

        * plain ``from openai import AsyncOpenAI`` (NO ``langfuse.openai`` wrapper) so the
          Bridge does NOT mint a duplicate generation — the plugin owns it server-side.
        * ``langfuse_prompt=`` is NOT sent (the plugin has no ``prompt=`` path; the managed
          prompt link is the known regression of any plugin-ON option — see
          spike/langfuse-plugin-d/MANAGED-PROMPT-DECISION.md).
        * ``extra_headers={"X-Hermes-Session-Id": H}`` with ``H = seed_for(run_id, gen_id)``
          so the plugin seeds its trace from our join key (plugin __init__:544 →
          ``create_trace_id(seed="H::H")``; #6 re-derives via
          :func:`eval_sdk.seed.derive_plugin_trace_id`).
        * DRIFT-DETECT (fail-open): read the echoed session id from the
          ``X-Hermes-Session-Id`` RESPONSE HEADER (the ``/v1/chat/completions`` path echoes it
          there, NOT in the body — see :meth:`_detect_session_drift`); if it != H the plugin
          trace will NOT match our derived id, so the obs join for THIS cycle is skipped
          (logged, NEVER raised). To read the header we go through
          ``chat.completions.with_raw_response.create`` (``.headers`` + ``.parse()``); the
          request body is byte-for-byte identical to Pattern A. ``import openai`` is still used
          only for the error TYPE so a transport failure maps to the same
          ``LLMUnavailableError`` as Pattern A.
        """
        try:
            import openai
            from openai import AsyncOpenAI
        except ImportError as exc:
            raise LLMUnavailableError(f"openai not installed: {exc}") from exc

        # H = the deterministic join key the plugin will seed its trace from. gen_id comes
        # from the situation the scheduler built this cycle; run_id is the shared run id.
        # A blank H (no run_id / no gen_id) is non-joinable -> skip the join (fail-open) but
        # STILL make the LLM call (the cycle must decide regardless of observability).
        h = self._plugin_session_id(situation)

        client = AsyncOpenAI(base_url=self._base_url, api_key=self._api_key or "no-key")
        create_kwargs = self._build_create_kwargs(situation)
        if h is not None:
            create_kwargs["extra_headers"] = {HERMES_SESSION_HEADER: h}
        try:
            # with_raw_response so the drift-detect can read the echoed session id from the
            # RESPONSE HEADER (the chat path echoes it there, not in the body). Same request
            # body as Pattern A; .parse() yields the identical typed completion.
            raw = await client.chat.completions.with_raw_response.create(**create_kwargs)
        except openai.OpenAIError as exc:
            raise LLMUnavailableError(f"hermes request failed: {exc}") from exc

        # Obs-only drift-detect: compare the echoed session id (response header) to H. A
        # mismatch (or a missing echo) means the plugin's minted trace will not equal our
        # derived id, so the score join would orphan -> note it and move on. This NEVER affects
        # the dispatched command (R-26: observability must not change actuation) and NEVER
        # raises into the cycle.
        if h is not None:
            self._detect_session_drift(raw, h)

        try:
            completion = raw.parse()
            content = completion.choices[0].message.content
        except (AttributeError, IndexError, TypeError) as exc:
            raise ValueError(f"unexpected completion shape: {exc}") from exc
        return parse_command_content(content)

    def _plugin_session_id(self, situation: dict) -> str | None:
        """``H = seed_for(run_id, gen_id)`` for the Option-D session header, or ``None``.

        ``None`` when ``run_id`` is unset/blank or the situation carries no usable ``gen_id``
        — then H is non-joinable, so the cycle still runs but the obs join is skipped (the
        score side independently fails open on a None trace, score_send.py).
        """
        run_id = self._run_id
        if not run_id or not run_id.strip():
            return None
        gen_id = situation.get("gen_id")
        if gen_id is None:
            return None
        return seed_for(run_id, gen_id)

    def _detect_session_drift(self, raw: object, expected: str) -> None:
        """Fail-open: warn if the echoed session id != H (the obs join would orphan).

        Best-effort read of the ``X-Hermes-Session-Id`` RESPONSE HEADER from the raw API
        response (``with_raw_response`` → ``.headers``). The ``/v1/chat/completions`` path
        echoes ``effective_session_id`` as that header, NOT a body field (the body
        ``session_id`` only exists on the ``/api/sessions/.../chat`` endpoint, api_server.py:
        1515) — so a body read would have mismatched on EVERY live cycle. ``.headers`` is the
        case-insensitive httpx mapping. Any read error or mismatch is logged and swallowed —
        this is observability only and must NEVER raise into the commander cycle (R-26
        fail-open, doc08:333).
        """
        try:
            headers = getattr(raw, "headers", None)
            echoed = headers.get(HERMES_SESSION_HEADER) if headers is not None else None
        except Exception:  # pragma: no cover - defensive; .get should not raise
            echoed = None
        if echoed != expected:
            log.warning(
                "Option-D session drift: echoed %s=%r != H %r; this cycle's score join "
                "WOULD ORPHAN (plugin seeded on a different id than the scorer re-derives) "
                "(fail-open, obs-only — the dispatched command is unaffected)",
                HERMES_SESSION_HEADER,
                echoed,
                expected,
            )
