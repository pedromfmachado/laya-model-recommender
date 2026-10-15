# Model Recommender

A Codex plugin that uses the local Laya classifier to recommend a model tier and reasoning effort for the first prompt in each session. It classifies task definition and reasoning demand independently, with a policy that favors **GPT-6 Luna · Medium** for straightforward work.

## Install

Requires the Codex CLI with hook plugin support on macOS or Linux. Run these commands in Terminal before opening Codex:

```sh
codex plugin marketplace add pedromfmachado/laya-model-recommender
codex plugin add model-recommender@model-recommender-plugins
```

The first local MCP startup downloads a private Python runtime, classifier, and model automatically. No Homebrew or Python installation is needed. The first setup requires internet access and can take several minutes; Codex allows up to 15 minutes for this initial startup. Later recommendations run locally and offline.

Open Codex, review and trust the hook in Hooks settings (or `/hooks` in the CLI), then start a new chat. If you use a custom `CODEX_HOME`, Codex uses that directory for its plugin cache and Model Recommender data.

If automatic setup fails, rerun it manually from Terminal using the installed plugin path:

```sh
sh "${CODEX_HOME:-$HOME/.codex}/plugins/cache/model-recommender-plugins/model-recommender/0.0.1/runtime.sh" setup
```

## Use

Submit a prompt in a new chat. On the first prompt in a session, the hook shows its recommended model and reasoning level.

- If the recommended model differs from your current model, choose **Use my current settings for this prompt.** or **Change settings, then continue with this prompt.** Choosing the second option blocks the prompt but keeps the original request in context. After changing settings, send a short follow-up, such as “Continue,” to resume it. If your model matches, the prompt continues with a short confirmation.

Recommendations run once per session, including resumed chats. The plugin leaves model and reasoning selection to you.

## Upgrade

Close Codex and run these commands in Terminal to fetch and install the latest version:

```sh
codex plugin marketplace upgrade model-recommender-plugins
codex plugin add model-recommender@model-recommender-plugins
```

Reopen Codex and start a new chat. The local MCP server checks its installed plugin files and automatically refreshes setup when they change, reusing the existing Python runtime and model cache. If startup setup fails, use the manual recovery command in the Install section, then start a new chat.

## Configuration

Edit the plugin's JSON files to customize recommendations:

- `questions.json` defines the `openai_model_tier` and `reasoning_effort_level` choice questions, prompts, and option descriptions.
- `router.json` defines the inference device, `questions_file`, `model_mapping`, and `reasoning_mapping`. Relative question-file paths resolve from the plugin directory.

The default mappings are:

| Model tier | Model |
| --- | --- |
| Luna | GPT-6 Luna |
| Sol | GPT-6.1 Sol |

| Reasoning level | Recommended setting |
| --- | --- |
| Mid | Medium |
| High | High |
| Extra High | Extra High |

Each question uses `type: "choice"`, a prompt, and option descriptions. Every model tier option needs an entry in `model_mapping`; every reasoning level option needs an entry in `reasoning_mapping`.

For a component overview and the startup-to-prompt communication flow, see [ARCHITECTURE.md](ARCHITECTURE.md).

Reinstall or refresh the plugin to apply changes, then start a new chat.

## Troubleshooting

If the runtime, classifier dependency, or checkpoint is unavailable, rerun setup from the plugin settings or ask Codex to **set up Model Recommender**. Setup stores its runtime and checkpoint under `${CODEX_HOME:-$HOME/.codex}/model-recommender`.

If a recommendation fails, Codex continues with the current settings. Start a new chat after fixing setup or configuration to retry.
