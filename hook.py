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


def claim_session(session_id):
    state_dir = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "model-routing" / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    marker = state_dir / hashlib.sha256(session_id.encode()).hexdigest()
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
    )


def build_output(recommendation, request):
    return {
        "decision": "block",
        "reason": (
            f"{recommendation}\n\n"
            "Ask the user whether to proceed with the original request using current settings. "
            "If they proceed, continue with the original request in the added context."
        ),
        "hookSpecificOutput": {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": f"Original user prompt:\n{request}",
        },
    }


def main():
    try:
        payload, request = read_request()
        if not claim_session(payload["session_id"]):
            output = {}
        else:
            config, capability, effort = classify(request)
            recommendation = format_recommendation(config, capability, effort)
            output = build_output(recommendation, request)
    except Exception as error:
        output = {"systemMessage": (
            f"Model recommendation failed: {type(error).__name__}: {error}. "
            "The task will continue using the current settings. Rerun setup if a dependency "
            "or classifier checkpoint is missing."
        )}
    print(json.dumps(output))


if __name__ == "__main__":
    main()
