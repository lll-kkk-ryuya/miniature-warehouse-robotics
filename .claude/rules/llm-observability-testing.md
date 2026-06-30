---
description: LLM/Hermes/Langfuse 周辺を変更するときの公式確認・テスト層・human gate 境界
paths:
  - docs/architecture/08-llm-bridge-common.md
  - docs/architecture/13-hermes-setup.md
  - docs/architecture/14-character-llm-negotiation.md
  - docs/architecture/20-dev-quality-and-testing.md
  - ws/src/warehouse_llm_bridge/**
  - ws/src/warehouse_orchestrator/**
  - tests/live/**
  - deploy/hermes/**
---

# LLM / Hermes / Langfuse テスト境界

LLM Bridge、Hermes Gateway、Langfuse tracing/score、provider call、Grok cost、managed-prompt を触る変更は、通常 CI と実トレース検証を明確に分ける。

## 公式一次情報の確認

- SDK/API の使い方、認証、endpoint、trace/score/cost の挙動を変更するときは、実装前に公式 docs を確認し、PR/Issue コメントか設計 docs に根拠リンクを残す。
- 優先する一次情報:
  - Langfuse OpenAI Python integration: https://langfuse.com/integrations/model-providers/openai-py
  - Langfuse SDK overview: https://langfuse.com/docs/observability/sdk/overview
  - Hermes Agent API Server: https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
  - OpenAI Chat Completions API reference: https://developers.openai.com/api/reference/resources/chat
- 公式 docs と本 repo の docs が食い違う場合、コードで暗黙に解釈せず、先に `docs/architecture/08` / `13` / `14` / `20` または該当 package `CLAUDE.md` を更新してから実装する。

## テスト層

1. **通常 CI / PR 必須**: fake client、noop tracer、pure unit を中心にする。外部 network、real provider call、real Langfuse、credential は必須化しない。
2. **Hermes live smoke**: Gateway 起動確認は `tests/live` の env-gated test に置く。例: `WAREHOUSE_LIVE_HERMES=1 python3.12 -m pytest tests/live/test_hermes_gateway_live.py`。`/health`、`/health/detailed`、認証境界、`API_SERVER_KEY`/`HERMES_API_KEY` がある場合の `/v1/models` までを確認する。
3. **課金 provider call**: `/v1/chat/completions` などの実 provider call は、追加 env（例: `WAREHOUSE_LIVE_HERMES_CHAT=1`）で明示 opt-in にする。通常 CI に入れない。
4. **Langfuse Phase 3 human gate**: 実 Langfuse trace/generation/score、二重 generation 無し、`trace_id`/`gen_id`/timestamp 突合、Grok cost、managed-prompt、SDK smoke は #88 の human/credential gate として扱う。Hermes smoke で代替しない。

## credential / 安全境界

- credential file は読まない。必要な値はユーザーが実行環境へ export した env var を使う。
- `API_SERVER_KEY`、`LANGFUSE_*`、provider key はログ・pytest failure・PR コメントに出さない。
- live test は skip-first にする。credential 不在や gateway 未起動は、通常 CI failure ではなく opt-in 実行時の明示 skip/fail とする。
- live ER の provider key が operator により恒久 env（`~/.zshenv` 等）にプロビジョン済なら **api-key 再入力は不要**（agent は `.env` を読まず・鍵値を出力/log/PR に出さない）。ただし env-gated live test（`WAREHOUSE_LIVE_ER=1`）は**有料 provider call**ゆえ、agent は実行前に**その batch/task の cost を operator に確認**してから走らせる（standing の無承認自走はしない・上の :37-39 と整合）。手順正本は `docs/dev/07-mode-x-er-live-e2e-runbook.md` §4.5。

## PR に残すこと

- どの層を実行したかを分けて書く: unit/fake、Hermes live smoke、provider chat、Langfuse trace。
- 実行しなかった live/human gate は、未検証として #88 などの追跡 issue に残す。
- SDK/API の挙動に依存した変更は、公式 docs の URL と確認日を PR 本文または issue コメントに残す。
