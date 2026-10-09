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


setup_guidance = (
    "Rerun Model Recommender setup from the plugin settings, or ask Codex to set up "
    "Model Recommender, then start a new chat. The task will continue using the current settings."
)

try:
    payload = json.load(sys.stdin)
    if not isinstance(payload, dict) or payload.get("hook_event_name", "UserPromptSubmit") != "UserPromptSubmit":
        raise ValueError("A UserPromptSubmit object is required")
    session_id = payload["session_id"]
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("A session id is required")
    prompt = payload["prompt"]
    if not isinstance(prompt, str):
        raise ValueError("A prompt is required")
    request = prompt.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not request:
        raise ValueError("A prompt is required")
except Exception:
    print("{}")
    sys.exit(0)

try:
    state_dir = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "model-routing" / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    session_key = hashlib.sha256(session_id.encode()).hexdigest()
    # Claim the session before inference so retries and concurrent hooks skip it.
    try:
        (state_dir / session_key).touch(exist_ok=False)
    except FileExistsError:
        print("{}")
        sys.exit(0)
except Exception:
    print(json.dumps({"systemMessage": "Model recommendation skipped because session tracking is unavailable. "
        "The task will continue using the current settings."}))
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
        print(json.dumps({"systemMessage": "Model Recommender needs its classifier checkpoint. " + setup_guidance}))
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
    suggestion = (
        f"The recommended model for this task is **{model}**. "
        f"The recommended reasoning level is **{reasoning}**. "
        "These recommendations are based on the following classifications:\n\n"
        f"- Capability demand: {capability_label} — {capability_reason}\n"
        f"- Reasoning demand: {effort_label} — {effort_reason}"
    )
    selected_model = normalize_setting(payload.get("model"))
    selected_reasoning = normalize_setting(payload.get("reasoning_effort"))
    if selected_reasoning is None:
        reasoning_setting = payload.get("reasoning")
        selected_reasoning = normalize_setting(
            reasoning_setting.get("effort") if isinstance(reasoning_setting, dict)
            else reasoning_setting
        )
    reasoning_aliases = {"low": "light", "xhigh": "extra-high"}
    selected_reasoning = reasoning_aliases.get(selected_reasoning, selected_reasoning)
    recommended_model = normalize_setting(model)
    recommended_reasoning = normalize_setting(reasoning)
    model_differs = selected_model is not None and selected_model != recommended_model
    reasoning_differs = selected_reasoning is not None and selected_reasoning != recommended_reasoning
    if model_differs or reasoning_differs:
        output = {"decision": "block", "reason": suggestion + (
            f"\n\nSelect **{model}** and **{reasoning}** manually in the model selector "
            "(or use `/model`), or keep your current settings. Then resubmit your original task "
            "to continue. Changing settings alone does not resume the task."
        )}
    else:
        if selected_model == recommended_model and selected_reasoning == recommended_reasoning:
            suggestion += "\n\nThe recommended model and reasoning level match the current settings."
        output = {"systemMessage": suggestion}
except ModuleNotFoundError:
    output = {"systemMessage": "Model Recommender needs its classifier dependency. " + setup_guidance}
except Exception:
    output = {"systemMessage": "Model and reasoning recommendation is unavailable. "
        "The task will continue using the current settings."}

print(json.dumps(output))
