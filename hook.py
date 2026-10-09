#!/usr/bin/env python3
"""Recommend a model and reasoning level on the first session prompt."""
import hashlib
import json
import os
import re
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

try:
    payload = json.load(sys.stdin)
    hook_event = payload.get("hook_event_name", "UserPromptSubmit")
    if hook_event == "UserPromptSubmit":
        session_id = payload["session_id"]
        if not isinstance(session_id, str) or not session_id.strip():
            raise ValueError("A session id is required")
        state_dir = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "model-routing" / "sessions"
        state_dir.mkdir(parents=True, exist_ok=True)
        session_key = hashlib.sha256(session_id.encode()).hexdigest()
        # Claim the session before inference so retries and concurrent hooks skip it.
        (state_dir / session_key).touch(exist_ok=False)
        request = payload["prompt"]
    elif hook_event == "Stop":
        message = payload.get("last_assistant_message")
        match = re.search(
            r"(?ms)^[ \t]*<proposed_plan>[ \t]*\r?\n(.*?)\r?\n[ \t]*</proposed_plan>[ \t]*(?=\r?\n|$)",
            message,
        ) if isinstance(message, str) else None
        if not match:
            print("{}")
            sys.exit(0)
        request = match.group(1).strip()
        if not request:
            print("{}")
            sys.exit(0)
    else:
        print("{}")
        sys.exit(0)
except Exception:
    print("{}")
    sys.exit(0)

try:
    config = json.loads(Path(__file__).with_name("router.json").read_text())
    questions = json.loads((Path(__file__).parent / config["questions_file"]).read_text())
    model_mapping = config["model_mapping"]
    reasoning_mapping = config["reasoning_mapping"]
    if set(questions) != {"capability", "effort"}:
        raise ValueError("Only capability and effort classification questions are supported")
    for name in ("capability", "effort"):
        question = questions[name]
        if question["type"] != "choice" or not question["instructions"].strip() or not question["criteria"]:
            raise ValueError("Capability and effort need explicit choice questions")
        if not all(isinstance(description, str) and description.strip() for description in question["criteria"].values()):
            raise ValueError("Every capability and effort option needs a definition")
    if set(model_mapping) != set(questions["capability"]["criteria"]) or not all(
        isinstance(model, str) and model.strip() for model in model_mapping.values()
    ):
        raise ValueError("Every capability option needs an explicit model mapping")
    if set(reasoning_mapping) != set(questions["effort"]["criteria"]) or not all(
        level in ("Light", "Medium", "High", "Extra High") for level in reasoning_mapping.values()
    ):
        raise ValueError("Every effort option must map to Light, Medium, High, or Extra High reasoning")

    from laya import Router
    from huggingface_hub.errors import LocalEntryNotFoundError

    try:
        result = Router(
            models={"english": sys.argv[1]} if len(sys.argv) > 1 else None,
            default="english", device=config["device"],
        ).predict({"request": request}, questions, model="english")
    except LocalEntryNotFoundError:
        if hook_event == "Stop":
            print("{}")
            sys.exit(0)
        print(json.dumps({"hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": "Briefly tell the user that Model Recommender needs its classifier checkpoint. "
                "Recommend rerunning Model Recommender setup from the plugin settings, or asking Codex to set up "
                "Model Recommender, then starting a new chat. Continue the current task.",
        }}))
        sys.exit(0)
    if result["usage"]["truncated"]:
        raise ValueError("Prompt did not fit")
    capability = result["answers"]["capability"]["choice"]
    effort = result["answers"]["effort"]["choice"]
    model = model_mapping[capability]
    reasoning = reasoning_mapping[effort]
    capability_label = capability.replace("_", " ").capitalize()
    effort_label = effort.replace("_", " ").capitalize()
    capability_reason = questions["capability"]["criteria"][capability]
    effort_reason = questions["effort"]["criteria"][effort]
    if hook_event == "Stop":
        system_message = (
            f"Recommended model for implementing this plan: **{model} · {reasoning}**. "
            "You can select the model and reasoning level manually in this chat.\n\n"
            f"- Capability demand: {capability_label} — {capability_reason}\n"
            f"- Reasoning demand: {effort_label} — {effort_reason}"
        )
        print(json.dumps({"systemMessage": system_message}))
        sys.exit(0)
    suggestion = (
        f"The recommended model for this task is **{model}**. "
        f"The recommended reasoning level is **{reasoning}**. "
        "These recommendations are based on the following classifications:\n\n"
        f"- Capability demand: {capability_label} — {capability_reason}\n"
        f"- Reasoning demand: {effort_label} — {effort_reason}"
    )
    context = (
        f"Post this recommendation message in the chat: {json.dumps(suggestion)}. "
        "Keep its classification bullets. Continue working on the user's task "
        "immediately without asking for confirmation or waiting for a settings change. "
        "The user handles model and reasoning selection manually."
    )
    current_model = payload.get("model")
    current_reasoning = payload.get("reasoning_effort")
    if not isinstance(current_reasoning, str):
        reasoning_setting = payload.get("reasoning")
        current_reasoning = (
            reasoning_setting.get("effort") if isinstance(reasoning_setting, dict)
            else reasoning_setting if isinstance(reasoning_setting, str)
            else None
        )
    normalize_setting = lambda value: "-".join(value.lower().replace("_", " ").split())
    reasoning_aliases = {"low": "light", "xhigh": "extra-high", "extra high": "extra-high"}
    selected_reasoning = (
        reasoning_aliases.get(normalize_setting(current_reasoning), normalize_setting(current_reasoning))
        if isinstance(current_reasoning, str) and current_reasoning.strip()
        else None
    )
    recommended_reasoning = reasoning_aliases.get(normalize_setting(reasoning), normalize_setting(reasoning))
    model_differs = (
        isinstance(current_model, str) and current_model.strip()
        and normalize_setting(current_model) != normalize_setting(model)
    )
    model_matches = (
        isinstance(current_model, str) and current_model.strip()
        and normalize_setting(current_model) == normalize_setting(model)
    )
    reasoning_differs = selected_reasoning is not None and selected_reasoning != recommended_reasoning
    both_settings_match = model_matches and selected_reasoning == recommended_reasoning
    if both_settings_match:
        context += (
            " After the recommendation, state that the recommended model and reasoning level "
            "match the current settings, and that Codex will proceed with the task."
        )
    if model_differs or reasoning_differs:
        action_prompt = (
            f"Please select **{model}** and **{reasoning}** manually in the model selector "
            "(or use `/model` to change the model), then click **Proceed**."
        )
        context = (
            f"First, post this recommendation message in the chat: {json.dumps(suggestion)}. "
            f"Then ask this separate action prompt before working on the task: {json.dumps(action_prompt)}. "
            "Do not repeat the recommendation or its classification bullets in the action prompt. "
            "If request_user_input_async and clock.sleep are both available, submit the action prompt "
            "using request_user_input_async with exactly one option: 'Proceed'. "
            "The async tool returns immediately; an accepted result only acknowledges "
            "submission and is not the user's answer. Keep the turn active while the question "
            "is pending: call clock.sleep with duration_ms=30000, check incoming user replies, "
            "and repeat the sleep until the user explicitly replies 'Proceed'. "
            "If a sleep expires without that reply, sleep again; there is no overall waiting "
            "deadline. Do not send a final message or end the turn while waiting, because "
            "turn completion discards the pending question in the desktop app. "
            "Do not work on the original task before the user's explicit 'Proceed' reply; "
            "tool acceptance, a preselected option, a dismissed question, elapsed time, "
            "or a settings change is not that reply. If either tool is unavailable, ask "
            "the question in chat, request only the reply 'Proceed', and wait for the "
            "user's next message before working on the original task. "
            "The user handles model and reasoning selection manually. After 'Proceed', "
            "continue the original task using the settings selected by the user."
        )
except ModuleNotFoundError:
    if hook_event == "Stop":
        print("{}")
        sys.exit(0)
    context = (
        "Briefly tell the user that Model Recommender needs its classifier dependency. "
        "Recommend rerunning Model Recommender setup from the plugin settings, or asking Codex to set up "
        "Model Recommender, then starting a new chat. Continue the current task."
    )
except Exception:
    if hook_event == "Stop":
        print("{}")
        sys.exit(0)
    context = (
        "Model and reasoning recommendation is unavailable. Briefly warn the user and continue using the current settings."
    )

print(json.dumps({"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": context}}))
