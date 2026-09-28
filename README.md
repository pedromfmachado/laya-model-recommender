# LiteLLM Laya model router

This project runs one LiteLLM Proxy container with an in-process Laya classifier. It exposes `gpt-5.6-luna`, `gpt-5.6-sol`, and `auto`. Direct model requests bypass classification. Auto routing uses four complexity tiers:

| Tier | Meaning | Model |
|---|---|---|
| `SIMPLE` | Straightforward questions or small transformations | `gpt-5.6-luna` |
| `MEDIUM` | Bounded coding or practical tasks with a few steps | `gpt-5.6-sol` |
| `COMPLEX` | Substantial multi-component work or difficult debugging | `gpt-5.6-sol` |
| `REASONING` | Deep analysis, formal reasoning, or difficult tradeoffs | `gpt-5.6-sol` |

## Start

Export the remote gateway credential, then start the proxy:

```sh
export LITELLM_API_KEY='…'
docker compose up --build
```

Compose requires `LITELLM_API_KEY` and passes it only as a container environment variable. It is not stored in `config.yaml`. The proxy listens on `127.0.0.1:4000`. During startup, the classifier loads `convaiinnovations/laya` before the proxy accepts requests. The first startup downloads the checkpoint; Docker volume `huggingface-cache` preserves it between restarts. If loading fails, startup fails so the problem is visible immediately.

The Docker build installs Laya and its Python dependencies, including large PyTorch/CUDA packages. The proxy imports Laya, downloads the checkpoint if needed, and loads it into memory during startup.

## Call the proxy

Use the OpenAI-compatible Chat Completions API:

```sh
curl http://127.0.0.1:4000/v1/chat/completions \
  -H 'Content-Type: application/json' \
  -d '{"model":"auto","messages":[{"role":"user","content":"Write a Python function to sort a list"}]}'
```

Use `gpt-5.6-luna` or `gpt-5.6-sol` as the request model to select a model directly. LiteLLM also supports `/v1/responses` in current releases; the remote gateway must support the endpoint and model capabilities for those requests to work end to end.

Classification reads the latest nonempty user text, ignoring assistant and tool messages. Empty input chooses `SIMPLE`. Inference, malformed-result, and timeout failures return `None`, which LiteLLM routes to the configured default model, Luna. To inspect the selected model, check LiteLLM logs or the `x-litellm-model-id` response header.

## Connect Codex

After starting the proxy, install the Laya-specific Codex profile and catalog:

```sh
./scripts/install_codex.sh
codex --profile laya
```

The installer overwrites only `laya.config.toml` and `laya.models.json` under `${CODEX_HOME:-~/.codex}`. It leaves Codex's default `config.toml` and `model_catalog.json` untouched.

## Local checks

```sh
python3 -m pip install -r requirements-test.txt
python3 -m pytest -q
LITELLM_API_KEY=dummy docker compose config
```

Live gateway authentication, available model aliases, and Responses API compatibility require valid gateway credentials and a live request.
