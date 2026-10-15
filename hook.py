#!/usr/bin/env python3
"""Recommend a model on a thread's first prompt and reasoning on every prompt."""
import hashlib
import json
import os
import select
import shutil
import subprocess
import sys
import time
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


def thread_marker(thread_id):
    state_dir = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex")) / "model-routing" / "sessions"
    state_dir.mkdir(parents=True, exist_ok=True)
    return state_dir / hashlib.sha256(thread_id.encode()).hexdigest()


def claim_thread(thread_id):
    marker = thread_marker(thread_id)
    try:
        marker.touch(exist_ok=False)
    except FileExistsError:
        return False
    return True


def pending_request_path(thread_id):
    return thread_marker(thread_id).with_suffix(".pending")


def save_pending_request(thread_id, prompt):
    pending_request_path(thread_id).write_text(prompt)


def read_pending_request(thread_id):
    path = pending_request_path(thread_id)
    return path.read_text() if path.is_file() else None


def clear_pending_request(thread_id):
    pending_request_path(thread_id).unlink(missing_ok=True)


def continuation_prompt(prompt):
    normalized = " ".join(prompt.lower().strip().rstrip(".!?…").split())
    return normalized in {
        "continue",
        "go ahead",
        "proceed",
        "resume",
        "continue with the original request",
        "continue with that request",
    }


def classify(request, include_model=True):
    config = json.loads(Path(__file__).with_name("router.json").read_text())
    questions = json.loads((Path(__file__).parent / config["questions_file"]).read_text())
    if not include_model:
        questions = {"effort": questions["effort"]}
    from laya import Router

    result = Router(
        models={"english": sys.argv[1]} if len(sys.argv) > 1 else None,
        default="english", device=config["device"],
    ).predict({"request": request}, questions, model="english")
    if result["usage"]["truncated"]:
        raise ValueError("Prompt did not fit the classifier")

    capability = result["answers"]["capability"]["choice"] if include_model else None
    effort = result["answers"]["effort"]["choice"]
    return config, capability, effort


def format_recommendation(config, capability, effort):
    model = config["model_mapping"][capability]
    reasoning = config["reasoning_mapping"][effort]
    return f"Recommended: {model} · {reasoning}", model, reasoning


def compare_model(payload, model):
    selected_model = normalize_setting(payload.get("model"))
    recommended_model = normalize_setting(model)
    return (
        selected_model is not None and selected_model != recommended_model,
        selected_model == recommended_model,
    )


def _app_server_start():
    codex = shutil.which("codex")
    if not codex:
        raise FileNotFoundError("Codex CLI is unavailable")
    process = subprocess.Popen(
        [codex, "app-server", "--listen", "stdio://"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        bufsize=0,
    )
    state = {
        "process": process,
        "buffer": bytearray(),
        "next_id": 1,
        "deadline": time.monotonic() + 4,
    }
    try:
        _app_server_request(
            state,
            "initialize",
            {"clientInfo": {"name": "model-recommender", "title": "Model Recommender", "version": "0.0.1"}},
        )
        process.stdin.write(b'{"method":"initialized","params":{}}\n')
    except Exception:
        process.terminate()
        try:
            process.wait(timeout=0.5)
        except subprocess.TimeoutExpired:
            process.kill()
        raise
    return state


def _app_server_request(state, method, params):
    process = state["process"]
    request_id = state["next_id"]
    state["next_id"] += 1
    process.stdin.write(
        (json.dumps({"id": request_id, "method": method, "params": params}) + "\n").encode()
    )
    deadline = min(time.monotonic() + 2, state["deadline"])
    while time.monotonic() < deadline:
        while b"\n" in state["buffer"]:
            line, _, rest = state["buffer"].partition(b"\n")
            state["buffer"] = bytearray(rest)
            try:
                response = json.loads(line)
            except (UnicodeDecodeError, json.JSONDecodeError):
                continue
            if response.get("id") == request_id:
                if "error" in response:
                    raise ValueError(f"app-server {method} failed")
                return response.get("result", {})
        remaining = deadline - time.monotonic()
        readable, _, _ = select.select([process.stdout], [], [], max(0, remaining))
        if not readable:
            break
        chunk = os.read(process.stdout.fileno(), 4096)
        if not chunk:
            break
        state["buffer"].extend(chunk)
    raise TimeoutError(f"app-server {method} timed out")


def _resolve_session_thread(state, session_id):
    response = _app_server_request(
        state,
        "thread/read",
        {"threadId": session_id},
    )
    root = response.get("thread")
    candidates = []
    cursor = None
    seen_cursors = set()
    for _ in range(5):
        params = {"limit": 20}
        if cursor:
            params["cursor"] = cursor
        listed = _app_server_request(state, "thread/list", params)
        threads = listed.get("data")
        if not isinstance(threads, list):
            return None
        candidates.extend(
            thread for thread in threads if thread.get("sessionId") == session_id
        )
        next_cursor = listed.get("nextCursor")
        if not next_cursor:
            cursor = None
            break
        if next_cursor in seen_cursors:
            return None
        seen_cursors.add(next_cursor)
        cursor = next_cursor
    if (
        cursor
        or len(candidates) != 1
        or candidates[0].get("id") != session_id
        or not isinstance(root, dict)
        or root.get("id") != session_id
        or root.get("sessionId") != session_id
    ):
        return None
    return root


def active_thread_settings(session_id, fallback_model=""):
    """Resolve an un-forked session root and read its reasoning setting."""
    state = None
    try:
        state = _app_server_start()
        thread = _resolve_session_thread(state, session_id)
        if not thread:
            return None, None
        effort = thread.get("reasoningEffort")
        if isinstance(effort, str) and effort.strip():
            return thread.get("id"), effort

        model = thread.get("model") or fallback_model
        if not isinstance(model, str) or not model.strip():
            return thread.get("id"), None
        models = _app_server_request(state, "model/list", {}).get("data", [])
        matching = [item for item in models if item.get("id") == model or item.get("model") == model]
        effort = matching[0].get("defaultReasoningEffort") if len(matching) == 1 else None
        return thread.get("id"), effort if isinstance(effort, str) and effort.strip() else None
    except (OSError, ValueError, TypeError, TimeoutError, AttributeError):
        return None, None
    finally:
        if state:
            process = state["process"]
            try:
                process.stdin.close()
                process.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                process.terminate()
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    process.kill()


def compare_reasoning(active, recommended):
    aliases = {"xhigh": "extra-high", "extra-high": "extra-high"}
    active_setting = normalize_setting(active)
    recommended_setting = normalize_setting(recommended)
    if active_setting is None or recommended_setting is None:
        return None
    return aliases.get(active_setting, active_setting) != aliases.get(
        recommended_setting, recommended_setting
    )
