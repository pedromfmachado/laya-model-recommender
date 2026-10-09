# Agent guidance

## Read before changing

- Classification or recommendations: read `questions.json`, `router.json`, and `hook.py` together. Keep classifier wording in `questions.json` and mappings in `router.json`.
- Setup or runtime: read `runtime.sh` and `skills/setup/SKILL.md`.
- Installation or releases: read `README.md`, `.codex-plugin/plugin.json`, and `.agents/plugins/marketplace.json`. Keep documented names and versioned paths consistent with metadata.

## Working conventions

- Keep changes narrow and follow the existing Python and shell conventions.
- Keep classifier questions short and concrete.
- Preserve the session guard, offline inference, JSON output, failure handling, and manual settings-selection flow unless the task requests changes.
- Do not add or run tests or classifier trials unless explicitly requested. Review source, configuration, and diffs; report unverified behavior accurately.
- Keep the README focused on current behavior, installation, use, configuration, and troubleshooting. Avoid duplicating code or JSON definitions in documentation.
- Source edits require plugin refresh or reinstallation and a new chat before they affect the installed plugin.
- Comments posted on the user's behalf must clearly identify them as written by Codex.
