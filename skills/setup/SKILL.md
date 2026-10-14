---
name: model-recommender-setup
description: Prepare or repair Model Recommender on this Mac or Linux computer when the user runs plugin setup or asks to set up Model Recommender.
---

Run the bundled setup script using its absolute path, resolved relative to this skill file: `../../runtime.sh`.

Execute `sh /absolute/path/to/runtime.sh setup`. The script downloads uv, private Python, Laya, and the model under `${CODEX_HOME:-$HOME/.codex}/model-recommender`. It requires internet access and may take several minutes on the first run. Let the script perform installation; the user does not need to install Python, Homebrew, or pip packages manually.

If the command fails, report the concrete error and resolve it within this setup's scope. Do not claim setup succeeded until the script exits successfully and prints `Model Recommender setup complete`.

After success, direct the user to review and trust the hook in Codex's Hooks settings (or `/hooks` in the CLI), then start a new chat. Keep the explanation brief. When the recommendation differs, the hook offers continuing with current settings or cancelling the prompt so the user can change settings manually and resubmit. Leave model and reasoning selection to the user.
