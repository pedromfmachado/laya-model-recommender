---
name: model-recommender-setup
description: Prepare or repair Model Recommender on this Mac or Linux computer when the user runs plugin setup or asks to set up Model Recommender.
---

The local MCP server runs setup automatically on first startup and whenever the installed plugin version or runtime source changes. For manual preparation or recovery, run the bundled setup script using its absolute path, resolved relative to this skill file: `../../runtime.sh`.

Execute `sh /absolute/path/to/runtime.sh setup`. The script downloads uv, private Python, Laya, and the model under `${CODEX_HOME:-$HOME/.codex}/model-recommender`. It requires internet access and may take several minutes on the first run. Let the script perform installation; the user does not need to install Python, Homebrew, or pip packages manually.

If the command fails, report the concrete error and resolve it within this setup's scope. Do not claim setup succeeded until the script exits successfully and prints `Model Recommender setup complete`.

After success, direct the user to review and trust the hook in Codex's Hooks settings (or `/hooks` in the CLI), then start a new chat. The existing Python runtime and model cache are reused. Keep the explanation brief. When the recommendation differs, the hook offers using current settings or changing settings before continuing with the prompt. If the user changes settings, the original request stays in context; they can send a short follow-up to continue it. Leave model and reasoning selection to the user.
