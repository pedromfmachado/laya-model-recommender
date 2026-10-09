# Model Recommender

A Codex plugin that uses the local Laya classifier to recommend a model and reasoning level for the first prompt in each session. It classifies capability and reasoning demand independently, with a policy that favors **GPT-6 Luna · Light** for familiar work.

## Install

Requires the Codex CLI with hook plugin support on macOS or Linux. Run these commands in Terminal before opening Codex:

```sh
codex plugin marketplace add pedromfmachado/laya-model-recommender
codex plugin add model-recommender@model-recommender-plugins
sh "$HOME/.codex/plugins/cache/model-recommender-plugins/model-recommender/0.1.3/runtime.sh" setup
```

Setup downloads a private Python runtime, classifier, and model automatically. No Homebrew or Python installation is needed. The first setup requires internet access and can take a few minutes; recommendations then run locally and offline.

Open Codex, review and trust the hook in Hooks settings (or `/hooks` in the CLI), then start a new chat. If you use a custom `CODEX_HOME`, replace `$HOME/.codex` in the setup command with that directory.

## Use

Submit a prompt in a new chat. The recommendation includes the suggested model and reasoning level, plus the selected capability and reasoning demand labels.

- On the first prompt in each session, the hook shows the recommendation and instructs Codex to ask you to choose **Proceed** or **Cancel** before acting. Proceed continues the original request with the current settings; Cancel stops it.

Recommendations run once per session, including resumed chats. The plugin leaves model and reasoning selection to you.

## Upgrade

Close Codex and run these commands in Terminal to fetch and install the latest version:

```sh
codex plugin marketplace upgrade model-recommender-plugins
codex plugin add model-recommender@model-recommender-plugins
```

Reopen Codex, review and trust the updated hook if prompted, then start a new chat. Your existing runtime and model are reused; rerun setup if the plugin requests it.

## Configuration

Edit the plugin's JSON files to customize recommendations:

- `questions.json` defines the `capability` and `effort` choice questions, instructions, and option descriptions.
- `router.json` defines the inference device, `questions_file`, `model_mapping`, and `reasoning_mapping`. Relative question-file paths resolve from the plugin directory.

The default mappings are:

| Capability demand | Model |
| --- | --- |
| Standard | GPT-6 Luna |
| Demanding | GPT-6.1 Sol |
| Exceptional | GPT-6 Astra |

| Reasoning demand | Reasoning level |
| --- | --- |
| Direct | Light |
| Deliberative | Medium |
| Intensive | High |

Each question must use `type: "choice"` with nonempty instructions and option definitions. Every capability option needs an entry in `model_mapping`; every effort option needs an entry in `reasoning_mapping`. Reasoning labels can be `Light`, `Medium`, `High`, or `Extra High`; the default policy stops at High. The recommendation displays both selected options.

Reinstall or refresh the plugin to apply changes, then start a new chat.

## Troubleshooting

If the runtime, classifier dependency, or checkpoint is unavailable, rerun setup from the plugin settings or ask Codex to **set up Model Recommender**. Setup stores its runtime and checkpoint under `${CODEX_HOME:-$HOME/.codex}/model-recommender`.

If a recommendation fails, Codex continues with the current settings. Start a new chat after fixing setup or configuration to retry.
