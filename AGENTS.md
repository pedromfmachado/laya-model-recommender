# Agent guidance

## Read before changing

- Classification or recommendations: read `questions.json` and `hook.py` together. Keep classifier wording and actual model/reasoning setting labels in `questions.json`; the MCP server uses those labels directly.
- Prompt flow or elicitation: read `hook.py`, `hooks/hooks.json`, and the `Use` section in `README.md` together. Keep displayed action labels aligned with their handling code and user guidance.
- Setup or runtime: read `runtime.sh` and `skills/setup/SKILL.md`.
- Installation or releases: read `README.md`, `.codex-plugin/plugin.json`, and `.agents/plugins/marketplace.json`. Keep documented names and versioned paths consistent with metadata.

## Working conventions

- Keep changes narrow and follow the existing Python and shell conventions.
- Keep classifier questions short and concrete.
- Preserve the session guard, offline inference, JSON output, failure handling, and manual settings-selection flow unless the task requests changes.
- When blocking a `UserPromptSubmit`, include the original request in `hookSpecificOutput.additionalContext` so a short follow-up can resume it after settings changes.
- Do not add or run tests or classifier trials unless explicitly requested. Review source, configuration, and diffs; report unverified behavior accurately.
- Treat the README as a concise user guide for installing, using, configuring, and troubleshooting the plugin. Update it only when a change affects what users need to know or do. Keep implementation details, internal processing steps, trial results, and development history out of it; avoid duplicating code or JSON definitions.
- Source edits require plugin refresh or reinstallation and a new chat before they affect the installed plugin.
- Comments posted on the user's behalf must clearly identify them as written by Codex.
