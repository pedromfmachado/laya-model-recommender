#!/usr/bin/env python3
"""Recommend a model and reasoning level on the first session prompt."""
import hashlib
import json
import os
import sys
from pathlib import Path

# Use the cached model only during prompt classification; setup enables downloads explicitly.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# Do not send Hugging Face Hub telemetry from the local classifier.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"


def normalize_setting(value):
    if not isinstance(value, str) or not value.strip():
        return None
    return "-".join(value.lower().replace("_", " ").replace("-", " ").split())


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
    questions = json.loads(Path(__file__).with_name("questions.json").read_text())
    from laya import Router

    result = Router(
        models={"english": sys.argv[1]} if len(sys.argv) > 1 else None,
        default="english", device="cpu",
    ).predict({"request": request}, questions, model="english")
    if result["usage"]["truncated"]:
        raise ValueError("Prompt did not fit the classifier")

    model = result["answers"]["capability"]["choice"]
    reasoning = result["answers"]["effort"]["choice"]
    return model, reasoning


def compare_model(payload, model):
    selected_model = normalize_setting(payload.get("model"))
    recommended_model = normalize_setting(model)
    return (
        selected_model is not None and selected_model != recommended_model,
        selected_model == recommended_model,
    )
