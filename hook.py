#!/usr/bin/env python3
"""Recommend a model and reasoning level on the first session prompt."""
import hashlib
import json
import os
import sys
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def normalize_setting(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return "-".join(value.lower().replace("_", " ").replace("-", " ").split())


def read_request():
    payload = json.load(sys.stdin)
    if payload.get("hook_event_name", "UserPromptSubmit") != "UserPromptSubmit":
        raise ValueError("Expected a UserPromptSubmit event")

    session_id = payload["session_id"]
    prompt = payload["prompt"]
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("Missing session id")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Missing prompt")
    return payload, prompt.replace("\r\n", "\n").replace("\r", "\n").strip()


def session_marker(session_id):
    state_dir = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "model-routing" / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / hashlib.sha256(session_id.encode()).hexdigest()


def claim_session(session_id):
    marker = session_marker(session_id)
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return False
    return True


def classify(request):
    config = json.loads(Path(__file__).with_name("router.json").read_text())
    questions = json.loads((Path(__file__).parent / config["questions_file"]).read_text())
    from laya import Router

    result = Router(
        models={"english": sys.argv[1]} if len(sys.argv) > 1 else None,
        default="english", device=config["device"],
    ).predict({"request": request}, questions, model="english")
    if result["usage"]["truncated"]:
        raise ValueError("Prompt did not fit the classifier")

    capability = result["answers"]["capability"]["choice"]
    effort = result["answers"]["effort"]["choice"]
    return config, capability, effort


def format_recommendation(config, capability, effort):
    model = config["model_mapping"][capability]
    reasoning = config["reasoning_mapping"][effort]
    return (
        f"Recommended: {model} · {reasoning}\n"
        f"Capability: {capability.replace('_', ' ').capitalize()}\n"
        f"Reasoning: {effort.replace('_', ' ').capitalize()}"
    ), model, reasoning


def build_output(payload, recommendation, model, reasoning, request):
    selected_model = normalize_setting(payload.get("model"))
    reasoning_setting = payload.get("reasoning")
    selected_reasoning = normalize_setting(payload.get("reasoning_effort")) or normalize_setting(
        reasoning_setting.get("effort") if isinstance(reasoning_setting, dict) else reasoning_setting
    )
    selected_reasoning = {"low": "light", "xhigh": "extra-high"}.get(
        selected_reasoning, selected_reasoning
    )
    model_differs = selected_model is not None and selected_model != normalize_setting(model)
    reasoning_differs = selected_reasoning is not None and selected_reasoning != normalize_setting(reasoning)

    if not model_differs and not reasoning_differs:
        if selected_model == normalize_setting(model) and selected_reasoning == normalize_setting(reasoning):
            recommendation += "\n\nYour current model and reasoning level match this recommendation."
        return {"systemMessage": recommendation}

    return {
        "decision": "block",
        "reason": (
            f"{recommendation}\n\n"
            "Your prompt is paused. To use these settings, select the recommended model and "
            "reasoning level, then reply ‘Proceed’. To continue with your current settings, "
            "reply ‘Proceed with current settings’. Your original prompt is saved, so you "
            "do not need to repeat it."
        ),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"Original user prompt:\n{request}",
        },
    }


def main():
    try:
        payload, request = read_request()
        marker = session_marker(payload["session_id"])
        if marker.exists():
            output = {}
        else:
            config, capability, effort = classify(request)
            recommendation, model, reasoning = format_recommendation(config, capability, effort)
            output = build_output(payload, recommendation, model, reasoning, request)
            if not claim_session(payload["session_id"]):
                output = {}
    except Exception as error:
        output = {"systemMessage": (
            f"Model recommendation failed: {type(error).__name__}: {error}. "
            "The task will continue using the current settings. Rerun setup if a dependency "
            "or classifier checkpoint is missing."
        )}
    print(json.dumps(output))


if __name__ == "__main__":
    main()
