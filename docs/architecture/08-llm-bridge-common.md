# LLM Bridge Node 技術設計 -- 共通設計

作成日: 2026-05-21
更新日: 2026-05-28

> **関連ドキュメント**:
> - [08a - LLM Bridge Mode A/B](../mode-a/08a-llm-bridge-mode-a.md) -- LLM単独交通管理
> - [08c - LLM Bridge Mode C](../mode-c/08c-llm-bridge-mode-c.md) -- LLM + Open-RMF
> - [12 - 共通基盤](12-infrastructure-common.md) -- Emergency Guardian, State Cache
> - [15 - MCPプラットフォーム](15-mcp-platform.md) -- Hermes Agent, Warehouse MCP Server, Policy Gate, 競合状態の防止
> - [14 - キャラLLM + 交渉プロトコル](14-character-llm-negotiation.md)
> - [11a - 交通管理 Mode A/B](../mode-a/11a-traffic-mode-a.md) | [11c - 交通管理 Mode C](../mode-c/11c-traffic-mode-c.md)

## 概要

LLM Bridge Node は、LLM API（Claude / ChatGPT / Gemini / Grok）と ROS 2 の間を仲介する ROS 2 ノード。ロボットの状態を構造化JSONにまとめてLLMに送り、LLMの判断をROS 2コマンドに変換する。

実装基盤としてHermes Agent Gateway + 自作 Warehouse MCP Serverを採用しており、実装方式の詳細は `15-mcp-platform.md` を参照。Nav2 MCP Server（ajtudela）は不採用（マルチロボット非対応・場所名非対応・モードCと不整合のため）。

### 前提条件

- Jetson Orin Nano がインターネットに接続されていること（LLM API呼出しに必要）
- WiFi環境: テザリング or ルーターで、ローカル通信（micro-ROS）とインターネット（LLM API）を同時利用
- Python 3.10+、rclpy、hermes-agent

## LLM Client インターフェース

LLMの差し替えを1行で行えるよう、共通インターフェースを定義する。

```python
from abc import ABC, abstractmethod

class LLMClient(ABC):
    """全LLM共通のインターフェース"""

    @abstractmethod
    def decide(self, situation: dict) -> dict:
        """状況JSONを受け取り、指示JSONを返す"""
        pass


class ClaudeClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        response = self.client.messages.create(
            model="claude-opus-4-8",  # 最新世代Opus（全Claude Opus統一、16-repository-and-conventions.md §7。新リリース時に更新）
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            messages=[{"role": "user", "content": json.dumps(situation)}]
        )
        return json.loads(response.content[0].text)


class ChatGPTClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        response = self.client.chat.completions.create(
            model="gpt-4o",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(situation)}
            ]
        )
        return json.loads(response.choices[0].message.content)


class GeminiClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        response = self.client.models.generate_content(
            model="gemini-2.5-flash",  # 2026/10/16に非推奨化。後継: gemini-3.5-flash（5倍高価）or gemini-3.1-flash-lite（安価）
            contents=json.dumps(situation),
            config={"system_instruction": SYSTEM_PROMPT}
        )
        return json.loads(response.text)


class GrokClient(LLMClient):
    def decide(self, situation: dict) -> dict:
        # xAI API は OpenAI互換インターフェース
        response = self.client.chat.completions.create(
            model="grok-4.3",  # ※モデル名・価格は2026-05-23時点で未確定
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(situation)}
            ]
        )
        return json.loads(response.choices[0].message.content)
```

**注意**: 上記のクラス定義は設計意図の説明用。実装では Hermes Agent の内蔵プロバイダーで4社を切り替えるため、これらのクラスを自作する必要はない（`12-infrastructure-common.md` 参照）。

切り替え（Hermes Agent 使用時）:

```yaml
# hermes config — active_provider を変更するだけ
active_provider: anthropic   # Claude
# active_provider: openai    # ChatGPT
# active_provider: google    # Gemini
# active_provider: xai       # Grok
```

## 場所名→座標変換テーブル

```python
LOCATIONS = {
    "shelf_1":          {"x": 0.2, "y": 0.3},
    "shelf_2":          {"x": 0.7, "y": 0.3},
    "shelf_3":          {"x": 1.2, "y": 0.3},
    "berth_A":          {"x": 0.2, "y": 0.8},
    "berth_B":          {"x": 0.7, "y": 0.8},
    "shipping_station": {"x": 0.2, "y": 0.1},
    "charging_station": {"x": 1.2, "y": 0.1},   # 物理1基。同時占有の排他は下流(Nav2/Open-RMF)想定・所有層 TODO、Policy Gate では未強制
    "retreat_A":        {"x": 0.45, "y": 0.85}, # 通路A yield 退避先
    "retreat_B":        {"x": 0.95, "y": 0.85}, # 通路B yield 退避先
}
```

※座標はジオラマの実測後に確定する。
※このテーブルのキーは `13-hermes-setup.md §3.3 config.yaml` の `locations`（Policy Gate の known_locations 検証用）と**完全に一致させること**。充電ステーションは1箇所（`charging_station`、物理1基。**同時占有の排他は Policy Gate では未強制＝所有層 TODO**（下流 Nav2/Open-RMF が担う想定。`policy_gate.validate_and_register_charging` 参照）、物理ジオラマ `04-diorama-layout.md` と一致）。`yield` の `retreat_to` はこの LOCATIONS のキー（`retreat_A`/`retreat_B`）を渡す（WAYPOINTS ではなく LOCATIONS で解決され、Policy Gate の known_locations でも検証される）。

## サイクル設計

サイクルは**レスポンス駆動**: 前回の応答受信（またはタイムアウト）後に一定時間待機して次サイクルを発火する。固定間隔ポーリングではない。待機時間は **config 駆動**: `cycle.mode_a_seconds`/`mode_c_seconds`（＝総サイクル長）を `warehouse_llm_bridge.scheduler.resolve_cycle_wait` が **待機=総−応答(~2s)** に変換して使う（環境別 overlay/`WAREHOUSE__*` 上書き可・doc19。欠落/不正/非正は code 既定 1.0/3.0s へ fail-open）。**dev（Gazebo・エンタメ回）は Mode A=`mode_a_seconds:120`（~2分スパン・`config/dev/warehouse.yaml`）に延長**し、目的（`WAREHOUSE_TASKS` シード）を与えて Nav2 が自走・約2分ごとに司令官が再評価する。延長時のトレードオフ＝司令官が交通管理も担う Mode A はデッドロック解消が最大~2分遅れる（衝突回避は Emergency Guardian 50ms+collision_monitor が独立に効き安全側不変・doc12 / 本節「Mode A / Mode C への適用」）。

### モード別サイクル長

| モード | 待機時間 | 総サイクル長 (応答2sの場合) | 理由 |
|---|---|---|---|
| **Mode A** | **1秒** | ~3秒 | 司令官が交通管理まで担当、反応速度を優先 |
| **Mode C** | **3秒** | ~5秒 | Open-RMFが即時調整、コスト最小化 |

待機時間の設計理由は「重なり防止」ではなく以下:
1. 状態変化粒度に合わせる（0.3m/s × 1〜3秒で意味ある状態差）
2. APIコストとレート制限
3. 動画的な思考ログ可読性
4. Phase 4 比較検証の公平性

重なり防止は A+B-3+C（クライアント側キャンセル + MCP gen_id 検証 + 冪等キー検証）で別解決済（本節「同時発火制御」参照）。

### タイムアウトとフォールバック

- **サイクル内タイムアウト**: 2.5秒（応答が2.5秒以内に返らなければ前回の指示を継続し、次サイクルへ進む）
- **HTTP接続障害**: 5秒以上応答なしで API 障害判断、Nav2 単体での自律走行にフォールバック（下記フォールバック設計参照）。**判定は時間基準（`cycle_wait` 非依存）**: タイムアウト(2.5s)の連続 `ceil(5/2.5)=2` 回で数える（`scheduler.OUTAGE_NO_RESPONSE_SEC`/`BridgeScheduler.__init__`）。応答後アイドル待機（`cycle_wait`＝config 駆動で最大~120s）は意図的アイドルで「無応答」に数えない。`nav2_only` は可観測フラグ（ループは毎サイクル再試行）。

### タイムライン例（Mode A の場合）

```
t=0.0s  LLM Bridge Node: current_gen += 1 → gen_store に publish
        State Cache JSON読取 + emergency情報付加
t=0.1s  LLM Bridge Node: POST → Hermes Gateway（situation JSON に gen_id 同梱）
t=2.0s  応答受信（正常時）→ Warehouse MCP Server経由で実行 → 1秒後に次のサイクル（Mode A）
t=2.5s  応答未受信 → クライアント側キャンセル（wait_for が in-flight httpx を中断）→ 前回指示を継続 → 1秒後に次のサイクル
        ↑ 採用トランスポートは Bridge 仲介の in-process dispatch（本節 採用実装📌）。
          tool 実行は Bridge 内で行われ、サーバー側（Hermes）に止めるべき tool 実行は
          無い（明示 /stop は #54 で撤回・R-35 part A 解消）
        ↑ 既に発火済の古い世代の tool call は MCP の gen_id 検証（B-3）で reject される
        ↑ 同一世代の重複・再送は MCP の冪等キー検証（C）で reject される
t=3.0s  次サイクル開始（Mode A）
```

Mode C の場合は最終行が `t=5.0s` になる。

## 同時発火制御（A+B-3+C: クライアント側キャンセル + gen_id検証 + 冪等キー）

本プロジェクトのサイクルは上記の通り**レスポンス駆動**（前回の応答受信後に待機時間 — Mode A:1秒 / Mode C:3秒 — カウント開始）であり、固定間隔ポーリングではない。したがって正常系では「LLM呼び出し中に次サイクルが発火する」競合は原理的に発生しない（Single-flight / Coalescing は不要）。

問題は **Hermes が LLM ↔ MCP の往復を内部で持つこと**にある。LLM の tool call は **Warehouse MCP Server で即時実行され、その時点で Nav2 等にコマンドが発行される**。Bridge が Hermes の最終応答を受け取った後に検証してももう手遅れである。よって対策は3層で行う（R-35 を踏まえた更新。従来は A+B-3 の2層だった）:

> 📌 **採用実装（S1, #4 / PR #70）= Bridge 仲介ディスパッチ（Bridge-mediated dispatch）**: 直前の「Hermes が LLM↔MCP の往復を内部で持つ＝サーバーサイド即時実行」は Hermes ネイティブのツール実行能力の説明であり、**本PJが S1 で採用したトランスポートではない**。凍結コード（`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/action_map.py` が tool call 毎に `idempotency_key` を mint・`ws/src/warehouse_mcp_server/warehouse_mcp_server/tools.py:11-13` がその引数を verbatim 受理・#41）では、**LLM は Command JSON を返し、Bridge が `action_map` で MCP ツール呼出に写像して自らディスパッチする**。レイヤ C（Bridge が mint する冪等キー）は **Bridge が tool call を仲介しないと実現不能**なため、この採用形が凍結契約上の正である（docs-first: 凍結契約 > 例示）。Hermes ネイティブのサーバーサイド実行経路は将来の代替（採用形では Bridge 仲介ディスパッチを用いるため不採用。明示 `/stop` は **Issue #54** で撤回済）。下表の3層は採用形（Bridge ディスパッチ）に対して機能する: B-3 の `gen_id` と C の `idempotency_key` はいずれも **Bridge が `action_map` で注入**する（system prompt の「gen_id を tool 引数に echo せよ」指示は Hermes ネイティブ経路向けの記述で、採用形では Bridge 注入が優先）。

> ▶ **S2-PR2 HALF B 実装更新（#4・トランスポート確定）**: 上記 Bridge 仲介ディスパッチを **in-process** で実現する＝Bridge は `warehouse_mcp_server.tools.WarehouseTools().dispatch` を直接呼出す（`ws/src/warehouse_llm_bridge/warehouse_llm_bridge/llm_bridge.py` が executor seam に注入。Bridge と MCP が同一 `gen_store`/`state_store` を共有 → B-3 と Policy Gate が end-to-end で効く）。同一トラック import は CI で track-aware に許可済（**#81**。`feat/llm-bridge` が `warehouse_llm_bridge`+`warehouse_mcp_server`+`warehouse_nav2_bridge` を所有＝`docs/architecture/16-repository-and-conventions.md:180`）＝stdio 子プロセス案は commander ループでは不要（`warehouse_mcp_server/server.py` の stdio entry は Hermes ネイティブ/外部 MCP client 用に存続）。**受理（`status=="ok"`）された motion tool（`dispatch_task`/`cancel_task`/`send_to_charging`）のみ** Warehouse MCP が Nav2 Bridge REST（`POST /api/v1/{navigate,wait,stop}`・`:8645`・**#86**・`docs/mode-a/12a-integration-mode-a.md:200-300`）へ forward する。stale(B-3)/dup(C)/Policy 拒否は `status!="ok"` で **0 POST**（=R-26 安全特性。`warehouse_mcp_server/tools.py` の単一 forward seam が enforce）。mapping は `docs/mode-a/08a-llm-bridge-mode-a.md:164-173`、`dropoff`→`destination` の凍結フィールドドリフトは `warehouse_mcp_server/nav2_client.py` の `plan_nav2_request` が明示変換（どちらの凍結契約も改名しない）。forward は **fail-open**（Nav2 Bridge outage / `.[nav2]`(httpx) 未導入はログのみ＝cycle を落とさない）。Mode C（open-rmf）は Open-RMF 経由なので forwarder 無し（`docs/architecture/15-mcp-platform.md:211-219`）。R-35A（明示 `/stop`）は **Issue #54** で撤回（採用トランスポートは in-process dispatch でサーバー側 tool 実行が無く /stop 不要）＝Layer A はクライアント側キャンセルのみ・安全主担保は B-3+C。

| 層 | 役割 |
|---|---|
| **A. クライアント側キャンセル** | Bridge タイムアウト（2.5s）時に `asyncio.wait_for` が in-flight httpx 要求をキャンセルする（接続クローズ）。**採用トランスポートは Bridge 仲介の in-process dispatch（本節 採用実装📌）でサーバー側（Hermes）の tool 実行が無いため、明示 `POST /v1/runs/{id}/stop` は不要**（#54 で撤回・R-35 part A 解消）。後続・古い tool call は B-3+C で reject される最善努力レイヤ |
| **B-3. MCP Server gen_id 検証（同一世代の単調比較）** | Bridge が cycle 開始時に `current_gen` を共有ストレージに書き込み、situation JSON に同値を含めて LLM に渡す。**Warehouse MCP の全ツール定義に `gen_id: int` を required 引数として追加**し、LLM に「gen_id を必ず引数に含めよ」と system prompt で指示。MCP は tool 呼び出し時に `args.gen_id` と共有ストレージの `current_gen` を比較し、**古い世代（`gen_id < current_gen`）なら reject**。同一世代内の重複は弾けない（→ C で補う） |
| **C. 冪等キー検証（tool-call 単位の UUID）** | 各 tool call に **1回限りの `idempotency_key`（UUID）** を Bridge が付与し、MCP は消費済みキーを `check_and_add` で記録する。**同一世代の重複・再送（replay）を弾く唯一の層**。B-3 が落とせない「同一 gen でゾンビセッションが同じ tool call を2回発火」を冪等に reject する（R-35 part B） |

A 単独では「タイムアウト直前に始まり、その後完了する tool call」を防げない。B-3 単独では「Hermes セッションがゾンビ的に LLM を呼び続け、tool call を連発する」事態のうち**同一世代内の重複**を防げない。C 単独では「次サイクルが既に進んだ後の古い世代」を世代番号だけで安く落とせない。**3層すべて必要**。

> ✅ **解決済（R-35 A / Issue #54）**: レイヤ A の明示 `POST /v1/runs/{id}/stop` は **撤回**した。採用トランスポートは同期 `POST /v1/chat/completions`（ステートレス・run_id 無し。doc13 §5.1/§5.3 = `13-hermes-setup.md:396-436` / doc15）であり、かつ tool dispatch は **Bridge 仲介の in-process（本節 採用実装📌）** なので、サーバー側（Hermes）に止めるべき run／tool 実行が存在しない（「接続断では止まらない」という R-35 part A の前提自体が当該経路では発生しない）。したがって **レイヤ A はクライアント側キャンセル（`asyncio.wait_for` による in-flight httpx 中断）のみ**とし、**安全の主担保は B-3（古い世代 reject）+ C（冪等キー）** に置く（unit: `test_stale_call_rejected_when_stop_noop_54` / `test_end_to_end_stale_generation_rejected` / `test_older_generation_rejected`、R-26）。Hermes ネイティブのサーバーサイド tool 実行経路を将来採用する場合に限り、その経路向けの明示キャンセル（実測要）を再検討する（本節 採用実装📌 の「将来の代替」）。

#### C. 冪等キー（`idempotency_key`）の設計

- **粒度は tool-call 単位**。`gen_id` は1サイクル＝1世代を表すため、司令官が1呼び出しで bot1・bot2 両方に指示する（navigate bot1 + navigate bot2）と**同一 `gen_id` の tool call が複数**正当に発火する。冪等キーを世代単位にすると正当な2台分指示を誤って弾く。よってキーは**各 tool call ごとに新規 UUID**。
- **bot1+bot2 のカーブアウト（最重要の正しさ条件）**: 同一 `gen_id` でも**キーが異なれば全て accept**。reject されるのは**同じキーの再送**のみ。MCP は `check_and_add(key, gen)` で「初見なら記録して True、既知なら False（冪等 reject）」を atomic に判定する。
- **Bridge が mint、LLM は echo しない（信頼の非対称性）**: `gen_id` は situation JSON 経由で LLM に渡し tool 引数として echo させる（B-3。本節の対策3層表および §「B案を B-3 にした理由」の「LLM／HTTP を信頼しない」論理）。しかし**「毎回ユニークで二度と繰り返さない UUID を必ず正しく生成・転記せよ」を LLM に委ねるのは信頼できない**（重複・取り違えが冪等性そのものを壊す）。よって冪等キーは **Bridge が tool call 送出時に注入**し、LLM 出力には含めない。**LLM 出力トークンを一切増やさない**（B-3 が増やす gen_id とは対照的）。
- **増殖の抑制**: MCP の消費済みキー記録は **gen-window（既定 8 世代）で eviction** し、古い世代のキーを忘れる。B-3 の単調世代とひも付き、ストレージの無限増殖を防ぐ。`current_gen > 記録時 gen + window` になったキーは破棄される。
- 契約: キーは `warehouse_interfaces` の `CommandItem.idempotency_key`（`str | None`、UUID 検証、省略可で後方互換）。MCP 側の消費記録は `IdempotencyStore.check_and_add`（`FileIdempotencyStore` が file-backed 実装）。MCP 側の実装詳細・tool schema・dispatch フローは `15-mcp-platform.md` の「競合状態の防止」§2 を参照。

### B案を B-3 にした理由（過去の訂正）

当初は HTTP ヘッダ `X-Bridge-Gen` を Hermes 経由で MCP に転送する方式を検討したが、**Hermes Gateway は OpenAI 互換 API を使用しており、クライアントの HTTP ヘッダが内部の MCP tool 呼び出しに転送される保証がない**。よって、より堅牢な「**tool schema の required 引数として gen_id を強制**」方式（B-3）を採用する。JSON Schema 検証で LLM の出力漏れを検出できるため信頼性が高い。詳細は `15-mcp-platform.md` の MCP Server gen_id 検証セクション参照。

### Mode A / Mode C への適用

| モード | コマンド頻度 | 本対策の重要度 |
|---|---|---|
| **Mode A** | 毎サイクル必ず（交通管理まで担当）| **必須**。遅延応答がデッドロック誘発しうる |
| **Mode C** | エスカレーション時のみ | A は推奨、B は必須（沈黙時に古いescalation応答が漏れて来うる）|

実装は両モード共通（MCPプラットフォーム層）。詳細は `15-mcp-platform.md` の「MCP Server gen_id 検証」を参照。

### 実装パターン（Bridge 側）

```python
class BridgeScheduler:
    def __init__(self, gen_store, cycle_wait_sec):
        self.current_gen = 0
        self.gen_store = gen_store      # MCP Server と共有（Redis / file / ROS param）
        self.cycle_wait_sec = cycle_wait_sec  # Mode A: 1.0, Mode C: 3.0

    async def run_cycle(self):
        """前回の応答受信後（またはタイムアウト後）に呼ばれる"""
        self.current_gen += 1
        gen = self.current_gen
        await self.gen_store.set(gen)         # B: MCP Server から読める場所に公開
        snapshot = build_situation()
        snapshot["gen_id"] = gen              # B-3: situation JSON にも明示
        try:
            # A: タイムアウト時に内部タスクごとキャンセル（shieldなし）
            response = await asyncio.wait_for(
                call_hermes(snapshot),
                timeout=2.5,
            )
            apply(response)
        except asyncio.TimeoutError:
            log.warn(f"cycle timeout gen={gen}, HTTP cancelled, continue previous command")
            # wait_for が内部タスクを cancel → httpx 接続クローズ（Layer A: client-side cancel）。
            # 採用形は Bridge 仲介の in-process dispatch（本節 採用実装📌）でサーバー側に
            # 止める run が無く、明示 /stop は不要（#54 で撤回・R-35 part A 解消）。
            # 既に発火済の古い世代の tool call は MCP の gen_id 検証で reject（B-3）。
            # 同一世代の重複・再送は MCP の冪等キー検証で reject（C）。
        await asyncio.sleep(self.cycle_wait_sec)
```

System prompt には以下を追加する:

```
状況JSON の `gen_id` フィールドを、すべての tool 呼び出しの `gen_id` 引数に必ずそのまま含めてください。これは安全機構です。
```

### 設計判断の要点

| 項目 | 採用 | 理由 |
|---|---|---|
| Single-flight / Coalescing | **不採用** | レスポンス駆動サイクルなので原理的に並列発火が起きない |
| A. クライアント側キャンセル（タイムアウト時） | **採用** | `asyncio.wait_for` が in-flight 要求をキャンセル。採用トランスポートは Bridge 仲介の in-process dispatch でサーバー側 tool 実行が無く、明示 `POST /v1/runs/{id}/stop` は不要（#54 で撤回・R-35 part A 解消）。後続・古い tool call は B-3+C で reject |
| B-3. MCP Server gen_id 検証（tool schema required引数）| **採用** | 既に発火済の**古い世代**の tool call が遅れて MCP に届くケースを止める。JSON Schema 検証で LLM の出力漏れも検出。**同一世代の重複は弾けない**（→ C で補完） |
| C. 冪等キー検証（tool-call 単位 UUID、Bridge mint）| **採用** | B-3 が落とせない**同一世代の重複・再送**を冪等に reject（R-35 part B）。Bridge が注入するため LLM 出力トークン増ゼロ。bot1+bot2 は別キーで全 accept |
| Priority Queue | **不採用** | YAGNI |
| イベント駆動エスカレーション | **不採用** | 次サイクルに `escalation` フィールドで投入する方式で十分（最大3秒遅延を許容） |

> **注**: 以前「Cancel & Restart 不採用」と書いていたのは「ユーザー入力で能動的に割り込む」想定の話。今回の「タイムアウト時の防御的キャンセル」は意味が違うため採用する。Langfuse trace に `cancelled` ステータスが混じるが、Phase 4 比較検証では集計から除外することで対応。

### ロックの粒度

司令官LLMは1人で2台分の指示を1呼び出しに含めるため、**Mode A / Mode C 共にグローバル1本のサイクルループ**を持つ（Bot単位ループは持たない）。会話演出のキャラLLMレイヤを追加する場合は、そのレイヤは司令塔とは別プロセス・別サイクルループで動かす。

なお、本セクションの仕組みはフォールバック設計表の「古い状況に基づく指示」項目に対応する実装手段である。

## 緊急状態の検出と対応

緊急事態の物理的安全はNav2（50ms）とOpen-RMF（即時）が担保する。Claudeは1-3秒の応答遅延があるため、緊急時の即時対応はできない。

| 緊急条件 | 即時対応（Nav2/Open-RMF） | Claudeへの通知 |
|---------|--------------------------|--------------|
| 2台が0.3m以内に接近 | Emergency Guardian（50ms周期）→ Nav2 cancel + cmd_vel停止 | `/emergency/event` 経由で次サイクルに付加 |
| ロボットがblocked > 10秒 | Emergency Guardian → Nav2リカバリー要求 | `/emergency/event` 経由で次サイクルに付加 |
| バッテリー ≤ 10% | Emergency Guardian → Nav2 cancel + cmd_vel停止 | `/emergency/event` 経由で次サイクルに付加 |
| バッテリー 10%超〜20%以下 | — | 次サイクルでbattery報告 → Claude判断 |

**Emergency Guardian（50ms周期、LLM非経由）が安全を担保する。** LLM Bridge Nodeのサイクル（Mode A: 3秒 / Mode C: 5秒）は戦略判断用であり、安全機能ではない。Emergency Guardianが発行した `/emergency/event` は State Cache Node経由でLLM Bridge Nodeに伝達され、次回のHermes Gateway POSTに緊急情報として付加される。詳細は `12-infrastructure-common.md` の安全レイヤー設計を参照。

## コマンドバリデーション（Policy Gate）

Claudeの指示はWarehouse MCP Server内の **Policy Gate** で検証される（`15-mcp-platform.md` 参照）。以下は論理的なチェック項目:

| チェック項目 | 動作 |
|------------|------|
| 場所名存在チェック | 不明な場所名は拒否 |
| 同一目的地に2台を送っていないか | 重複タスクを拒否 |
| ロボット状態チェック（stale/unavailable） | stale時はdispatch拒否、cancel/chargingは許可 |
| バッテリーポリシー（≤10%全拒否、≤20%新規タスク禁止。`warehouse_interfaces.safety` が単一ソース） | Policy Gateが強制 |
| Emergency中のロボットへの指示 | 拒否 |
| レートリミット | 短時間の連続コマンドを拒否 |

## フォールバック設計

| 異常 | 検出方法 | 動作 |
|------|---------|------|
| LLM API サイクル内タイムアウト（2.5秒） | レスポンス待ち時間 | 前回の指示を継続、次サイクルへ |
| LLM API 接続障害（5秒超応答なし） | HTTP接続タイムアウト | Nav2単体で自律走行を継続 |
| LLM API エラー（500等） | HTTPステータス | Nav2単体で自律走行を継続 |
| 不正なJSON返答 | json.loads失敗 | 無視して次回リクエスト |
| 未知のaction | action名チェック | 無視してログ記録 |
| 存在しない場所名 | LOCATIONS辞書チェック | 無視してログ記録 |
| 物理的に不可能な移動 | Nav2が拒否 | Nav2の安全機構に委任 |
| インターネット切断 | 接続チェック | Nav2フォールバックモードに移行 |
| 同一目的地への2台送信 | Policy Gate（Warehouse MCP Server内） | タスク拒否 |
| 古い状況に基づく指示 | A. クライアント側キャンセル + B-3. MCP tool schema required gen_id | 古い tool call を MCP 層で reject、次サイクルで再判断（「同時発火制御」セクション参照） |

## 比較検証ログ（Langfuse統合）

比較検証のログ記録には Langfuse（LLMオブザーバビリティプラットフォーム）を使用する。Hermes Agent のビルトインプラグインとして標準搭載されており、環境変数の設定のみで全LLM呼出しが自動記録される。

### 比較の公平性 — Hermes Memory / Skills は Phase 4 比較で強制 OFF（#103 / R-36）

Phase 4 の4社 LLM 比較は**公平性の前提として、比較 run では Hermes の自己学習（長期記憶 `memory`・過去会話想起 `session_search`=FTS5・`skills`）を強制的に無効化する**。自己学習エージェントは同一プロンプトでも応答が変動し、**記憶・学習の差が「LLM の能力差」と切り分け不能**になって再現性が崩壊するためである（[R-36](../shared/07-research-notes.md)）。実 Hermes の OFF は [`13-hermes-setup.md`](13-hermes-setup.md) §「Memory / Skills / session_search — 比較公平性のための OFF 機構」の **3ノブ**（`memory.memory_enabled: false` ＋ `user_profile_enabled: false` ／ `skills.creation_nudge_interval: 0` ／ `platform_toolsets` から `memory`/`skills`/`session_search` を除外）を**比較 run の Hermes config で設定**して達成する。Bridge 側はこの運用意図を起動時に検証する fail-closed ガードを担う（下記「実装」）。

> **注（#103 で実キーへ修正）**: 旧記載の `memory.enabled` / `skills.enabled` は実在しないキーだった。実デプロイ済みのスキーマは上記の `memory.memory_enabled` / `user_profile_enabled` ＋ `skills.creation_nudge_interval`（`deploy/hermes/gcp/config.yaml:474-560`）。凍結された運用契約（Phase 4 強制 OFF）は不変で、ここではキー参照のみを実体へ整合させている。「Memory(FTS5)」は category error で、FTS5 は `memory` ではなく `session_search` toolset。

| 用途 | memory / skills / session_search | 根拠 |
|------|----------------|------|
| **Phase 4 4社比較 run** | **強制 OFF**。実 OFF=Hermes config 3ノブ（`memory.memory_enabled:false`＋`user_profile_enabled:false` ／ `skills`・`session_search` toolset 除外＋`creation_nudge_interval:0`）。Bridge は比較 run 設定の自己矛盾を起動時に assert＋OFF をログ（**公平性ガード**） | 公平性・再現性（R-36） |
| **Mode A エンタメ回**（比較対象外） | **ON 可**（「AI が学習・成長する」演出。比較指標には載せない） | 動画演出（Mode A=エンタメ） |
| **Mode C 検証回 / WO 統合** | 比較に準じ **OFF を既定**（倉庫 intent の既定 OFF が継承） | Open-RMF が交通を担い LLM はタスク割当のみ |

- **学習スコープの制約**: memory / skills / session_search に学習させてよいのは**タスク割当パターンのみ**。**交通制御スキルは禁止**（安全に関わる判断を自己学習で変動させない。[`06-implementation-phases.md`](06-implementation-phases.md) Phase 0.5 検証項目）。Bridge は Hermes 側の skill 登録を強制できないため、本制約は docs/コメントでの明文化＋（露出するなら）Hermes config の toolset 絞り込みで担保する。
- **実装（#103, Phase 3→4, `warehouse_llm_bridge`）**: 倉庫 config（`config/<env>` の `hermes.memory_enabled` / `skills_enabled` / `comparison_run`、既定 OFF）に運用意図を宣言し、**Bridge は `comparison_run` 下で memory/skills の ON 宣言を起動時に abort＋OFF をログ**（公平性ガード。`warehouse_interfaces/config.py` の `_validate_safety` と同型の**独立**チェック）。**ただし Bridge↔Hermes はステートレス `/v1/chat/completions` で Bridge は Hermes の memory 状態を制御・確認できない**ため、これは intent/consistency ガードであり、**実 OFF は上記 Hermes config 側が権威**（倉庫 intent→Hermes config の実配線は Phase 4 起動ハーネス責務として defer）。Hermes 内蔵 Memory の MCP トークンコスト影響（`allowed_tools`/toolset 絞り込み要否）は **Phase 3→4 で実測**（現状は config 由来の概算: `memory_char_limit` 2200≈800tok ＋ `user_char_limit` 1375≈500tok を毎セッション注入）。**契約変更なし**（`warehouse_interfaces` 不変、Hermes 側機能 + Bridge 起動ガード）。

### Langfuse 設定

```bash
export LANGFUSE_PUBLIC_KEY=pk-lf-xxx
export LANGFUSE_SECRET_KEY=sk-lf-xxx
export LANGFUSE_BASE_URL=https://cloud.langfuse.com   # Hermes 内蔵プラグインが読む env。Bridge SDK は LANGFUSE_HOST、Orchestrator は HERMES_LANGFUSE_*（doc13:128 / doc19:78）
```

Hermes Agent が自動的にLangfuseに送信する内容（コード変更不要）:
- 1 trace / ターン（3秒サイクルの1回分）
- 1 generation / LLM API呼出し（入力JSON、出力JSON、トークン数、レイテンシ、コスト）
- 1 observation / ツール呼出し（navigate_to_pose等の引数と結果）

Langfuseはfail-open設計: SDK未インストール・認証エラー・通信障害時はサイレントにスキップし、エージェントループに影響しない。

### DecisionLog とLangfuseの対応

```python
@dataclass
class DecisionLog:
    turn: int                    # → Langfuse trace name
    timestamp: str               # → Langfuse 自動記録
    llm_name: str                # → Langfuse provider/model
    situation: dict              # → Langfuse generation input
    response: dict               # → Langfuse generation output
    response_time_ms: int        # → Langfuse latency（自動）
    reasoning: str               # → Langfuse output 内の reasoning フィールド
    commands: list               # → Langfuse output 内の commands フィールド
    result: str                  # → Langfuse score（自作で追加）
    task_completion_time: float  # → Langfuse score（自作で追加）
```

上記のうち `result` と `task_completion_time` のみ自作コードで Langfuse に送信する:

```python
# Nav2ゴール到達後にスコアを送信（Langfuse Python SDK v4 / 4.9.0）
from langfuse import get_client
langfuse = get_client()
langfuse.create_score(trace_id=current_trace_id, name="result", value="success",
                      data_type="CATEGORICAL",
                      metadata={"robot": "bot1", "mode": "A", "provider": "claude", "gen_id": gen_id})
langfuse.create_score(trace_id=current_trace_id, name="task_completion_time", value=29.3,
                      data_type="NUMERIC",
                      metadata={"robot": "bot1", "mode": "A", "provider": "claude", "gen_id": gen_id})
langfuse.flush()  # 短命スコアラ（#6 wo）はプロセス終了前に flush 必須（バッファ未送出を防ぐ）
```

> **v2→v4 注意（要 docs-first）**: 旧 `langfuse.score(...)` は **v3 rewrite で削除**。現行は `create_score(...)`（`get_client()` 経由）。score は tag を持てないため `robot`/`mode`/`provider`/`gen_id` は **score の metadata に複製**する（pin した版で `create_score(metadata=)` が未対応なら score 名に robot を埋める＝`result_bot1`、または per-robot observation に紐付け）。`trace_id` は §7.5（doc13）の **32hex no-dash・両脚同一リテラル**を使う。score は trace 生成前でも ingest 可（後で同一 `trace_id` でリンク＝結果整合）。

> **📌 採用実装（#83/#92, wo `score_send.py`）**: 上の metadata は**例示**。実装は additive に **`run_id`**（trace seed の前半 `f"{run_id}:{gen_id}"`、#73 / doc13 §7.5(b)）も metadata に持つ＝`{run_id, mode?, provider?, gen_id?}`（`robot` は efficiency leg が per-leg 付与）。`provider` は run-level ラベルで **env `WAREHOUSE_PROVIDER`**（§セッション命名規則 / doc08:367 の通り）から解決。`run_id` 空/全空白は unset 扱いで送信ゲート（trace 導出不可なら no-op）。docs-first（例示 vs 凍結）に従い逐語一致は主張しない。

これにより自作の DecisionLog ファイル出力は不要となり、全データが Langfuse Dashboard で閲覧・比較・分析できる。

### trace 所有 — Bridge が所有（推奨・Pattern A）

Hermes ビルトイン Langfuse に generation 所有を任せると、Bridge は自前 `trace_id` / `metadata` / managed-prompt をネイティブに乗せられない（pass-through 未確認）。よって **Bridge が `from langfuse.openai import AsyncOpenAI`（async＝scheduler が `await` で呼び `wait_for` でキャンセル可。Layer A）を `base_url`=Hermes（OpenAI 互換）で用い、自分で generation/trace を所有**する（4社を単一コードパスで叩く比較公平性を保ったまま trace 所有問題を解消）。二重計上回避のため **Hermes 側 Langfuse プラグインは無効化**する（[doc13 §7.5](13-hermes-setup.md)）。

**採用実装（#78 + Prompt Management / env tag additive）**: 各 turn の `trace_id` は **`create_trace_id(seed=f"{run_id}:{gen_id}")`（決定的・32hex no-dash, doc13 §7.5(b)）** で採番し（#6 が同一 id を独立導出）、`session_id` / `tags=[provider, mode, "prompt:<name>", env=<v>]` を trace に付与する。`prompt:<name>` は managed-prompt 取得結果（または code fallback）を表す trace-only discriminator、`env=<v>` は deployment 環境タグ `env=dev`/`env=stg`/`env=prod`（doc13:526,607 の `env=` 慣用、値は `warehouse_interfaces/paths.py` の `warehouse_env()`＝`WAREHOUSE_ENV` 由来）である。score は tag 不可ゆえ prompt/env とも未対応＝trace-only。emit list 上は `env=<v>` を末尾に置くが、Langfuse は保存時に tag order を正規化しうるため fetch/filter は順序でなく**値**で行う。metadata は `gen_id` / `trace_id` に加え、Prompt Management 用の `{prompt_name, prompt_version, prompt_source, mode_label}` を付与する。tool 呼出は各々 span 化する（1 observation/tool, §比較検証ログ）。managed-prompt（Langfuse Prompt Management）連携の方針は **本ドキュメント後半の §Langfuse Prompt Management 方針（採用）** で確定する。**Provider access の決定（Vertex AI SDK 不採用・Hermes 単一経路）は [doc13 §7.6](13-hermes-setup.md)**。

### セッション命名規則

```python
# 採用実装（#78, warehouse_llm_bridge/tracing.py build_session_id）
session_id = f"run_{mode}_{provider}_{scenario}_{ts}"
# 例: run_none_claude_deadlock_20260715_1430
# run_id == session_id（trace seed の前半。doc13 §7.5(b)）
```

同一シナリオの4社比較がLangfuse上でフィルタ・比較可能。`mode`=`traffic_mode`、`provider`/`scenario` は run-level ラベル（env `WAREHOUSE_PROVIDER`/`WAREHOUSE_SCENARIO`）。

### 比較指標

| 指標 | Langfuseでの取得方法 | 重要度 |
|------|---------------------|--------|
| 応答速度 | generation.latency（自動） | 高 |
| 判断の正確性 | score "result"（自作送信） | 高 |
| タスク完了時間 | score "task_completion_time"（自作送信） | 高 |
| 効率性 | ロボットの総移動距離（自作計算→score送信） | 中 |
| 推論の質 | generation.output.reasoning（自動、人間評価） | 中 |
| コスト | generation.cost（自動計算） | 低 |
| エラー率 | generation.error（自動検出） | 高 |

### デプロイ方針

| フェーズ | Langfuse環境 | 理由 |
|---------|-------------|------|
| Phase 0-6（本プロジェクト） | Langfuse Cloud（無料、50K obs/月） | 導入30秒、規模十分（約500 obs/デモ） |
| 将来のPhysical AI展開 | Langfuse セルフホスト（Docker） | データ外部送信不可の環境向け |

移行時の変更: `LANGFUSE_BASE_URL`（Hermes が読む env）を自社サーバーに変更するのみ。

## コスト見積もり

10分デモあたりの司令官LLM呼出回数（モード別）:

| モード | サイクル | 呼出回数/10分 |
|---|---|---|
| Mode A | ~3秒 | 約200回 |
| Mode C | ~5秒 | 約120回 |

### トークン消費の前提

| 項目 | 推定値 |
|---|---|
| System prompt | 約550 tokens |
| Situation JSON（2台分・gen_id・履歴含む） | 約1000 tokens |
| MCP tool 定義（7ツール、gen_id含む） | 約550 tokens |
| **入力合計 / call** | **約2000 tokens** |
| 出力（reasoning + commands JSON） | 約200 tokens |

### コスト推定（実測前の暫定値）

| LLM | モデル | 入力単価 | 出力単価 | Mode A 1デモ (~200回) | Mode C 1デモ (~120回) |
|---|---|---|---|---|---|
| Claude | Opus（最新世代） | $15/MTok | $75/MTok | **~$9.0** | **~$5.4** |
| ChatGPT | GPT-4o | $2.50/MTok | $10/MTok | ~$1.40 | ~$0.84 |
| Gemini | 2.5 Flash | $0.30/MTok | $2.50/MTok | ~$0.22 | ~$0.13 |
| Grok | 4.3 | 未確定 | 未確定 | ~$1.50（推定） | ~$0.90 |

4社合計: **Mode A ~$12/デモ、Mode C ~$7/デモ**（実測前の暫定推定値。Claude を Opus 単価で再計算、16-repository-and-conventions.md §7）。

> ⚠️ **注意**: 以前は1呼出あたり ~600 tokens で試算していたが、MCP tool 定義 (約550 tokens) と gen_id 機構の追加で実質約 2000 tokens に増えた。Phase 0.5 (Gazebo) で実測してから本数値を確定する。Phase 4 の 4社比較本番では **$40-50 / 1セット (4社) の予算枠**を確保しておくこと（Claude を Opus 単価で再計算したため上方修正、16-repository-and-conventions.md §7）。

別途、キャラLLM（Opus、Bot1/Bot2）のコストを別計上（Phase 4 比較対象外）。旧 Haiku 設計から全 Claude Opus 統一に変更したため、キャラLLM もトークン単価は司令官と同じ Opus 単価。応答テンポへの影響は要実測（`16-repository-and-conventions.md` §7 検討事項）。詳細は `15-mcp-platform.md` 参照。

**注意**: Gemini 2.5 Flash は**2026年10月16日**に非推奨化（2026-05-26調査で確認。当初「6月」としていたのは gemini-2.0-flash の話）。後継は `gemini-3.5-flash`（$1.50/$9.00/MTok、約5倍高価）。安価代替として `gemini-3.1-flash-lite`（$0.25/$1.50/MTok）が利用可能。移行はモデル名1行変更のみ。Phase 4まで2.5-flashで問題なし。

※緊急時の安全対応はEmergency Guardian（50ms周期、LLM非経由）が担当し、LLM Bridgeのサイクル長（Mode A: 3秒 / Mode C: 5秒）は変動しない。LLM呼出し回数への影響はない。

## ROS 2 トピック

### 購読（入力）

| トピック | 型 | 用途 |
|---------|-----|------|
| `/bot{n}/odom` | `nav_msgs/Odometry` | オドメトリ（移動量・速度） |
| `/bot{n}/amcl_pose` | `geometry_msgs/PoseWithCovarianceStamped` | AMCL推定位置（地図上の位置） |
| `/bot{n}/scan` | `sensor_msgs/LaserScan` | 障害物検知 |
| `/bot{n}/battery` | `sensor_msgs/BatteryState` | バッテリー残量 |

### 発行（出力）

| トピック | 型 | 用途 |
|---------|-----|------|
| `/bot{n}/goal_pose` | `geometry_msgs/PoseStamped` | Nav2ゴール送信（※doc12設計ではWarehouse MCP Server/Fleet Adapterが発行。LLM Bridge Nodeは直接発行しない） |
| `/bot{n}/cmd_vel` | `geometry_msgs/Twist` | 停止指令（速度0）（※doc12設計ではEmergency Guardianが発行） |
| `/llm/reasoning` | `std_msgs/String` | LLM判断理由（表示用） |
| `/llm/command` | `std_msgs/String` | LLM指示JSON（ログ用） |

## 開発順序

1. **Phase 0.5（Gazebo）**: Hermes Gateway + Warehouse MCP Server + Claude でGazebo上テスト
2. **Phase 3（実機）**: Gazebo版をベースに実機用に微調整
3. **Phase 4（比較）**: ChatGPT / Gemini / Grok Provider を追加、ログ記録・分析

## Python依存パッケージ

```
# requirements.txt
hermes-agent>=0.14.0
rclpy
```

注: LLM SDK（anthropic, openai, google-genai, xai）は hermes-agent の依存関係として自動インストールされる。

## 比較計測の追加設計

> 本節は上の [§比較指標](#比較指標)（Phase 0.5 からの基本指標 ＋ 既存 frozen score `result`(CATEGORICAL)/`task_completion_time`(NUMERIC)/`efficiency`(NUMERIC)）を **Phase 3-4 向けに拡張**する。既存の file:line 参照を保つため、§比較指標 本体ではなく doc 末尾（References 直前）に配置している。
>
> **いずれも凍結契約 `warehouse_interfaces` の変更不要** — 全ラベルは Langfuse score の metadata `{run_id, mode?, provider?, gen_id?}` に載る（score は tag を持てない、§比較検証ログ :365）。`provider` 軸は env `WAREHOUSE_PROVIDER`（:367）、`trace_id` は #4/#6 が `create_trace_id(seed=f"{run_id}:{gen_id}")` で導出（[doc13](13-hermes-setup.md):485、ROS 契約ではなく Langfuse/Audit 突合キー）。

### 追加比較スコア（司令官比較軸, Phase 3-4）

| score 名 | data_type | データ源 | 比較軸 | Phase | 状態 |
|---|---|---|---|---|---|
| `collision_free` | BOOLEAN | run/シナリオ単位: 当該 run 中に Emergency Guardian の `near_collision` イベント（[doc12](12-infrastructure-common.md):101 / :236）が**発生しなかった**ら `true`。※真の「接触」イベントは存在せず、近接 0.3m 停止（:101 / :109-110）の不在で代理する | 司令官（provider）＋交通モード軸 | Phase 3+（実機/sim 要） | ⚠️ **Phase依存・暫定**（信号源 = sim 接触センサ vs Guardian `near_collision` 近接停止が未配線） |
| `replans` | NUMERIC（run/タスク単位の回数） | Nav2 のリプラン回数（`nav2_bridge`/BasicNavigator が露出する想定）。**現状どの凍結契約・トピックも産出しない**（nav-traffic #8 未露出） | 司令官＋交通モード軸 | Phase 3（Nav2 稼働要） | ⚠️ **Phase依存・暫定**（データ源未露出） |
| `mean_decision_latency` | NUMERIC（ms, run 単位の集計） | 司令官の各ターン `generation.latency`（自動取得 :390 / :343）を run 単位で平均（派生集計） | 司令官比較（provider） | Phase 4（集計） | 確定（auto latency からの派生） |
| `deadlock` | NUMERIC（run 単位のデッドロック検出回数＝頻度） | Mode A/B 検出（[doc08a](../mode-a/08a-llm-bridge-mode-a.md):271-281＝2台が `status=="idle"`＋`current_task` 保持 ＋ 距離<0.4m ＋ heading 対向>2.5rad。#55/#128 で `status=="blocked"` 依存から利用可能信号へ**再基礎付け済**＝:281）／ Mode C は Open-RMF エスカレーション→Claude 介入（[doc11c](../mode-c/11c-traffic-mode-c.md):149-150） | 交通モード軸（デッドロック頻度, [doc06](06-implementation-phases.md):275） | Phase 3+（live 計測） | ⚠️ **Phase依存・暫定**（信号源は #55/#128＝doc08a:281 で確定済、live 計測は実機/sim 要） |

- **Mode A 交渉スコア**（`negotiation_rounds` / `agreement_reached`）は **演出専用・Phase 4 比較対象外**（[doc14](14-character-llm-negotiation.md):255 / [doc06](06-implementation-phases.md):263）。定義は **[doc14 §交渉スコア](14-character-llm-negotiation.md#交渉スコア)** に置く（交渉エピソードの記述指標であり provider 能力比較には用いない）。

### Grok コスト定義（PLAN・実検証は Phase 3, [doc13](13-hermes-setup.md) §7.5② :520）

`コスト`（:395 = `generation.cost`）は Langfuse が generation の `model` 文字列を価格表に正規表現マッチして算出する。**Langfuse 既定の価格表は OpenAI/Anthropic/Google のみで xAI Grok を含まない**ため、Grok は cost が空欄になり比較が破綻する（doc13:520② を確認）。対策（**本節は PLAN・実装は Phase 3**）:

1. **Langfuse にカスタムモデル価格を登録**（UI: Project Settings → Models → Add model definition ／ API: `POST /api/public/models`）。`match_pattern`（Grok の model 文字列への正規表現、例 `(?i)^(xai/)?grok-4.*$`）＋ 入出力トークン単価（USD/token、xAI 公開価格・取得日を併記）＋ `unit: TOKENS`。ユーザ定義は組込より優先。
2. **オフライン フォールバック（wo, Phase 前でも可）**: `usage_details`（入出力トークン数）は cost と独立に取得されるため、wo 側で `tokens × 静的 xAI 価格表`（versioned 定数）から cost を派生計算できる → Langfuse 価格登録の有無に依存せず Grok 比較を解錠。
3. **Phase 3 実検証**: 登録後 `grok-*` の `generation.cost_details.total > 0` を assert（4社とも cost ≠0・比較可能を確認）。

> **要実機検証（未確定）**: v4 価格フィールドの正確な形（`prices:{input,output}` ネスト vs flat `input_price`/`output_price`）と Hermes が Grok に転送する literal `model` 文字列は、コード化前に live で確認する（推測で固定しない）。

### 集計設計 — Metrics API + Datasets/Experiments（PLAN・実装 Phase 4, [doc13](13-hermes-setup.md):472「12構成×KPI」）

**12構成 = 4 provider × 3 交通モード（none/simple/open-rmf）**。集計は **Phase 4 seam**（本節は設計 PLAN・凍結契約ではない）:

- **Datasets + Experiments（推奨）**: 5 比較シナリオ（[doc06](06-implementation-phases.md):249-253）を **Dataset** 化し、**provider×mode = 12 runs**（`run_name="claude__open-rmf"` 等）として Experiment 実行 → 同一 versioned dataset 上で compare view が run 間を差分表示（再現性ある4社×シナリオ比較）。ad-hoc な trace tag より系統的。
- **Metrics API**（`GET /api/public/v2/metrics`、view `scores-numeric`/`scores-categorical`、集計 sum/avg/count/min/max/p50–p99）。**v4 制約**: score は tag を持たず（:365）、`sessionId`/`traceId` は**フィルタ可だが group-by 不可**（v2）。→ 12構成の軸は **score `name` に符号化**（例 `result__claude__open-rmf`）するか、構成ごとに filter したクエリを `name` で group-by して 12 回反復する。**score metadata / sessionId への group-by 依存は避ける**。
- **wo 側コード seam（Phase 4・新規）**: ① per-run KPI を構成軸付き score 名で export、② Metrics API を叩く query helper（12 filtered クエリを `name` で group-by → 表組立）。KPI 値は wo が算出（Nav2/diagnostics）、Langfuse は保存・集計のみ。Langfuse 側 config（Dataset・ScoreConfig・dashboard・compare view・run 命名規約）はコード外。

> **要 Phase 3/4 検証（未確定）**: v4 score の **metadata group-by 可否**（不可なら上記 name 符号化を採用）。

### Langfuse Prompt Management 方針（採用）

> 司令官システムプロンプトの編集・版管理を Langfuse へ移す方針。trace 所有（§trace 所有 — Bridge が所有）からの後方参照先。**末尾に追記**＝既存の file:line 参照を行ズレさせないため（[#165 教訓](../dev/03-retrospectives.md)）。

司令官システムプロンプトの**編集・版管理は Langfuse Prompt Management を正本**とし、コードへの直書き管理から降りる。Phase 4 の4社比較で「同一プロンプトを版で固定して全 provider に等しく当てる」運用を、コード変更・再デプロイなしに行うための採用である（公平性 R-36 / §比較検証ログ :305-318）。**本文の設計上の出所は [doc08a](../mode-a/08a-llm-bridge-mode-a.md):231-265,316-335（Mode A/B）と [doc08c](../mode-c/08c-llm-bridge-mode-c.md):138-180（Mode C）の例示プロンプト**（いずれも例示・凍結契約でない）；運用版は Langfuse が持つ。

- **命名（実送信単位 = 1 Langfuse prompt）**: Mode A/B = `warehouse-commander-mode-ab`（= 旧 `SYSTEM_PROMPT` + `MODE_A_RULES` の合成。本番で base 単独送信は無いので1本に集約）、Mode C = `warehouse-commander-mode-c`（= 旧 `MODE_C_PROMPT`）。キャラLLM人格は将来 `warehouse-character-persona`（[doc14](14-character-llm-negotiation.md)・本移行のスコープ外）。`type="text"`（倉庫レイアウトは situation JSON=user message で渡るため system prompt は静的＝テンプレ変数なし）。
- **取得とラベル**: **起動時に1回** `get_client().get_prompt(name, label, fallback, cache_ttl_seconds).prompt`（`warehouse_llm_bridge/prompts.py` の `resolve_commander_prompt`）で取得し `HermesClient` に保持する。**版更新の反映はノード再起動**（per-cycle 再取得は将来拡張・現状は起動1回取得）。**比較 run は `label="production"` に版を pin**（公平性 :305-318）。`cache_ttl_seconds`（既定 300）は単発取得時の SDK キャッシュTTL（起動1回取得のため runtime の再取得トリガにはならない＝版更新は再起動で反映）。
- **prompt の self-describing（seed config）**: seed 時に各 Langfuse prompt の `config` に `traffic_modes`（`mode-ab`→`["none","simple"]` / `mode-c`→`["open-rmf"]`）を記録し、Langfuse UI で「この prompt はどの mode 用か」が prompt 単体で分かるようにする（`model`/`temperature` と同居・doc13:171 で routing は Hermes 側ゆえ advisory）。
- **fail-open（安全網・二段）**: 取得失敗は全て**コードのフォールバック定数へ縮退**しロボットデモを止めない（Langfuse fail-open :333 と一体・`resolve_commander_prompt` は never-raise）。二段構成: **(1) SDK レベル `fallback=`**（unreachable / not-found 時に SDK が `is_fallback=True` のオブジェクトを返す）、**(2) その他全例外**（langfuse 未導入＝ImportError / auth / 空 body 等）を outer `except` で捕捉。いずれもコード定数へ。フォールバックは旧 `build_system_prompt(mode)` の合成＝直書き定数を「管理元」から「障害時フォールバック」へ役割降格したもの。**コードのフォールバックと Langfuse seed は同一出所**（このフォールバック定数）から作り drift を防ぐ（seed: `python -m warehouse_llm_bridge.seed_prompts` が `create_prompt` で upsert・既定 dry-run）。
- **generation 紐付け（任意・Pattern A 整合）**: 取得した prompt オブジェクトを `langfuse.openai` の `chat.completions.create(..., langfuse_prompt=obj)` に渡し generation に紐付ける（prompt 単位の分析）。フォールバック時（`is_fallback`）は紐付けない。`.parse()` 経路は未対応（本 PJ は `.create()` のみ使用）。
- **trace タグ（mode + どの prompt/env かを trace で可視化）**: 各 turn の trace に `tags=[provider, mode, "prompt:<name>", env=<v>]` と `metadata={prompt_name, prompt_version, prompt_source, mode_label}` を付与する（eval_sdk `LangfuseTracer` の generic `extra_tags`/`extra_metadata` 経由＝domain 非依存の additive 拡張）。**mode は元から trace tag**、加えて **prompt 名（mode を内包＝`-mode-ab`/`-mode-c`）と deployment env（`env=dev`/`env=stg`/`env=prod`）で trace をフィルタ可能**にする。`prompt_version` は managed 取得時のみ（fallback は `None`）、`prompt_source` は `langfuse`/`code`（＝実際に送られたのが managed 版かコード fallback か）、`mode_label` は cryptic な bare mode 値（none/simple/open-rmf）の人間可読版（例 `Mode A (LLM単独交通管理)`）＝tag の bare 値は Phase-4 比較軸として温存しつつ metadata で可読化。env tag は trace-only（score metadata には未追加）。
- **gen_id echo 行**: system prompt 内の gen_id 注記は採用形（Bridge 注入 :167）では advisory。Langfuse 版でも文面はそのまま据え置く（意味変更しない）。
- **config**: `hermes.prompts`（`source: langfuse|code` / `label` / `cache_ttl_seconds` / `names.{mode_ab,mode_c}`）。**実体は `config/warehouse.base.yaml`**（hermes config schema の正本は [doc13 §3.3](13-hermes-setup.md)。同節の例 YAML には未掲載＝additive 追加）。`source: code` は完全に従来の直書き合成のみ（Langfuse 不使用＝CI/完全 offline 用）。
- **live 検証は Phase 3 #88**: 実 Langfuse 4.9.x への seed・取得・`langfuse_prompt=` 紐付けは [doc13 §7.5](13-hermes-setup.md) ④ の live 確認項目（人間ゲート）。offline（fallback 経路・seed `--dry-run`・unit）は本実装で緑。

## References

- [Anthropic API Documentation](https://docs.anthropic.com/) -- 参照日: 2026-05-21
- [OpenAI API Reference](https://platform.openai.com/docs/api-reference) -- 参照日: 2026-05-21
- [Google AI Gemini API](https://ai.google.dev/gemini-api/docs) -- 参照日: 2026-05-21
- [rclpy -- ROS 2 Python Client Library](https://docs.ros2.org/latest/api/rclpy/) -- 参照日: 2026-05-21
- [Langfuse -- 公式サイト](https://langfuse.com/) -- 参照日: 2026-05-23
- [Langfuse -- GitHub](https://github.com/langfuse/langfuse) -- 参照日: 2026-05-23
- [Hermes Agent -- Built-in Plugins (Langfuse)](https://hermes-agent.nousresearch.com/docs/user-guide/features/built-in-plugins) -- 参照日: 2026-05-23
