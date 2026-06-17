# LLM / Hermes / Langfuse Testing Guidance

Source reference: `.claude/rules/llm-observability-testing.md`.

## Principle

Changes around LLM Bridge, Hermes Gateway, Langfuse tracing/scores, provider calls, Grok cost, or managed prompts must keep default CI offline and deterministic, while live verification remains explicit and env-gated.

## Official Sources

Before changing SDK/API behavior, authentication, endpoints, trace attributes, score/cost semantics, or managed-prompt wiring, check official primary docs and cite the source in the PR, Issue comment, or project docs:

- Langfuse OpenAI Python integration: https://langfuse.com/integrations/model-providers/openai-py
- Langfuse SDK overview: https://langfuse.com/docs/observability/sdk/overview
- Hermes Agent API Server: https://hermes-agent.nousresearch.com/docs/user-guide/features/api-server
- OpenAI Chat Completions API reference: https://developers.openai.com/api/reference/resources/chat

If official docs conflict with repository docs, update `docs/architecture/08`, `13`, `14`, `20`, or the relevant package `CLAUDE.md` before implementation. Do not resolve the conflict silently in code.

## Test Layers

1. **Default CI / PR gate**: use fake clients, noop tracers, and pure unit tests. Do not require external network, real provider calls, real Langfuse, or credentials.
2. **Hermes live smoke**: keep gateway checks in env-gated `tests/live`. Example: `WAREHOUSE_LIVE_HERMES=1 python3.12 -m pytest tests/live/test_hermes_gateway_live.py`. Cover `/health`, `/health/detailed`, auth boundaries, and authenticated `/v1/models` only when `API_SERVER_KEY` or `HERMES_API_KEY` is exported.
3. **Paid provider calls**: require an extra opt-in env such as `WAREHOUSE_LIVE_HERMES_CHAT=1` for `/v1/chat/completions`. Never make this a default CI gate.
4. **Langfuse Phase 3 human gate**: real trace/generation/score, duplicate-generation checks, `trace_id`/`gen_id`/timestamp joins, Grok cost, managed prompts, and SDK smoke remain tracked under #88. Hermes smoke does not replace this gate.

## Credentials

- Do not read credential files unless the user gives an explicit scoped request.
- Do not print `API_SERVER_KEY`, `LANGFUSE_*`, or provider keys in logs, test failures, PR comments, or issue comments.
- Live tests should skip by default when credentials are absent. Missing credentials must not fail normal CI.

## Reporting

In PRs and issue comments, report executed verification by layer: unit/fake, Hermes live smoke, provider chat, Langfuse trace. Anything not run because it needs credentials, cost, real gateway state, or real Langfuse should be listed as a remaining human gate.
