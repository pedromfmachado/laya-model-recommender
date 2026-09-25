# Local Laya router for Codex

This Python proxy routes each Codex user turn through a local Laya classifier to a LiteLLM model alias. Tool continuations retain the turn's selected model and reasoning effort. The proxy listens only on `127.0.0.1` and supports standard Responses JSON and HTTP SSE.

## Run it without an API

Python 3.11 or newer is required. The offline demo needs neither a checkpoint download nor API credentials:

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[test]'
python laya_proxy.py demo
```

In another terminal:

```sh
curl http://127.0.0.1:8787/healthz
curl http://127.0.0.1:8787/v1/responses \
  -H 'Content-Type: application/json' \
  -d '{"model":"laya-auto","input":"Hello"}'
```

The response contains **`[DEMO — no model inference]`** and confirms the offline fixture is working. The demo returns valid Responses messages, SSE events, and a deterministic `demo_echo` function call when that tool is supplied. It does not generate answers to arbitrary questions. It uses a deterministic fixture classifier and an in-process HTTPX mock upstream while exercising the real proxy server. Demo mode never contacts LiteLLM or sends `LITELLM_API_KEY`.

The standalone smoke helper starts a separate loopback server, checks JSON, streamed tool calls, tool results, and a category-changing follow-up, then stops its server:

```sh
python scripts/smoke_demo.py
```

Use `--port 18788` if the helper's default port is occupied. This smoke test does not validate Laya accuracy or the production gateway.

## Connect LiteLLM

```sh
python -m pip install -e '.[laya,test]'
python laya_proxy.py serve --routes routes.example.toml
```

The default complete Responses URL is **`https://litellm.data.odyssey.preview.nwse.cloud/v1/responses`**, derived from the supplied `/v1` base URL. An explicit `--upstream-responses-url https://host.example/custom/responses` overrides it literally; the proxy never adds path segments to an override. Optional authentication comes only from the `LITELLM_API_KEY` environment variable. Populate it using your usual secret manager or shell environment, not a command argument or TOML file. Caller authorization headers are not forwarded.

Laya loads `convaiinnovations/laya` once at startup. Set `classifier.checkpoint` in TOML to another compatible checkpoint or a local directory. First startup may download the model. The separate `[laya]` dependency extra keeps the offline demo lightweight. If loading fails, startup logs an explicit fallback condition and `/healthz` reports `status: degraded`, `mode: fallback`, and `fallback: general`. Health describes local readiness, not upstream availability.

The gateway must actually provide the supplied aliases and support their canonical GPT capabilities:

| Category | Alias | Effort |
|---|---|---|
| coding | `gpt-5.6-sol` | medium |
| debugging | `gpt-5.6-sol` | high |
| research | `gpt-5.6-terra` | high |
| writing | `gpt-5.6-luna` | low |
| general/fallback | `gpt-5.6-luna` | medium |

Edit descriptions, aliases, efforts, and the confidence threshold in `routes.example.toml`, or supply your own TOML. Only `routes.general.model` is mandatory; categories without aliases inherit it. The public model name is always `laya-auto`; other names receive HTTP 400. Codex's requested reasoning effort is replaced by the category effort; other reasoning fields are preserved.

Unreachable production upstreams receive an honest HTTP 502 API error. The proxy does not substitute demo content when production inference is unavailable. Upstream errors and status codes otherwise pass through.

## Install the Codex profile

```sh
python laya_proxy.py install-codex-profile --routes routes.example.toml
# Start the proxy first, then:
codex --profile laya
```

Installation exports the installed Codex binary's complete descriptors with `codex debug models --bundled`, using `gpt-5.6-sol` as the template. Override with `--template-model`, `--codex-bin`, or `--catalog-source /path/to/export.json`. `--codex-home /tmp/isolated-codex` supports isolated verification. Existing generated files require `--overwrite`.

The installer creates `~/.codex/laya.config.toml` and `~/.codex/laya.models.json`; it leaves `config.toml` untouched. The catalog contains one **Laya Auto** entry. It preserves the full descriptor and ordinary tool/input semantics while disabling Responses Lite, WebSocket transport, experimental tools/context, hosted search, and service tiers. Both advertised context limits are clamped to the smallest declared model context window (272,000 in the supplied configuration). The profile uses `wire_api = "responses"` and `requires_openai_auth = false`.

Every configured alias needs explicit `[models."alias"]` canonical identity, context window, input modalities, tools, and reasoning-effort declarations for installation. Periods in aliases require quoted TOML table keys. These are capability requirements for the gateway, not a claim that the live gateway has been tested. The installed Codex version must support separate profile files. Desktop integration is outside this version's acceptance criteria.

## Routing and privacy

Only usable user text enters classification. System/developer messages, assistant text, tools, images, and recognized injected AGENTS/environment instruction envelopes are excluded. Laya sees the latest request plus bounded preceding context; the full original Responses request is forwarded with only `model` and `reasoning.effort` changed. Token limits are clamped to the checkpoint's own limits with space reserved for category options and instructions. A single dedicated inference thread serializes loading, tokenization, and prediction even when an HTTP request is canceled.

Decisions are cached using conversation identity when present and normalized user-history hashes. Response IDs recover routes for tool-only continuations. Explicit conflicting conversation identities cannot borrow routes. New user input after a known response ID triggers a new decision, including repeated identical text. Unknown tool continuations and requests with no usable text use general. Without conversation identity or response ancestry, identical full user histories are intentionally indistinguishable. Caches are bounded in memory; a restart or eviction loses ancestry, causing general fallback for tool-only requests.

The threshold defaults to 0.70 and uses Laya's `answer_confidence` (selected-option probability), not its entropy-based `confidence` field. Low confidence, malformed predictions, inference errors, and unavailable checkpoints choose general. Logs contain route, alias, effort, duration, fallback reason, and upstream status without prompts or keys. Third-party loading errors are reduced to a generic status to avoid logging secrets. HTTP client environment proxies are disabled. Client disconnects cancel pending upstream requests and close streams; there are no retries or model switches midstream.

## Verification and evaluation

```sh
python -m pytest -q
python scripts/smoke_demo.py
python laya_proxy.py evaluate --routes routes.example.toml
```

Tests cover routing/fallback, token budgeting, injected-context filtering, same-turn single-flight behavior, conversation isolation, canceled inference serialization, profile generation, JSON/SSE and tool forwarding, credentials, upstream errors, and real socket cancellation before response headers, during JSON reads, and during SSE.

`evaluation/prompts.jsonl` includes 24 labeled examples across the five categories, including ambiguous follow-ups. The evaluation command reports raw classifier accuracy, final routing accuracy, and fallback frequency separately. `--demo` evaluates only deterministic fixtures and labels its results accordingly. This is an illustrative set, not a calibrated benchmark; use representative prompts before adopting the 0.70 threshold. The initial real-checkpoint run routed 37.5% correctly with 75% fallback, indicating the default descriptions/threshold need workload-specific evaluation before relying on specialized routing.

Production compatibility still requires live tests against the supplied LiteLLM gateway: a tool-using Codex task, a follow-up changing category, and a long conversation triggering compaction. Offline tests cannot establish live gateway authentication, canonical alias behavior, or compaction compatibility. This implementation provides only `/v1/responses` and `/healthz`; an environment that requires an additional compaction endpoint needs separate compatibility work before end-to-end support can be claimed.
