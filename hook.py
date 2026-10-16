"""Local MCP tool used by the synchronous prompt-submit hook."""
import hashlib
import json
import logging
import os
import sys
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP, Context

# Laya loads the classifier through Hugging Face; these flags prevent model downloads during prompts.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
# Classification is local, so there is no need to send Hugging Face Hub telemetry.
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

logging.basicConfig(level=logging.WARNING)
mcp = FastMCP("model-recommender")


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


def _remove_schema_title(schema):
    schema.pop("title", None)


class PromptChoice(BaseModel):
    model_config = ConfigDict(json_schema_extra=_remove_schema_title)

    action: str = Field(
        description="Choose an action.",
        json_schema_extra={
            "enum": [
                "Use my current settings for this prompt.",
                "Change settings, then continue with this prompt.",
            ]
        },
    )


def hook_output(decision=None, reason=None, system_message=None, additional_context=None):
    result = {}
    if decision:
        result["decision"] = decision
    if reason:
        result["reason"] = reason
    if system_message:
        result["systemMessage"] = system_message
    if additional_context:
        result["hookSpecificOutput"] = {
            "hookEventName": "UserPromptSubmit",
            "additionalContext": additional_context,
        }
    return result


@mcp.tool()
async def recommend_for_prompt(
    ctx: Context,
    prompt: str,
    session_id: str,
    model: str = "",
) -> dict:
    """Classify the first session prompt and ask what to do when settings differ."""
    marker = None
    try:
        if not prompt.strip() or not session_id.strip():
            raise ValueError("Missing prompt or session id")
        marker = session_marker(session_id)
        if marker.exists():
            return hook_output()

        suggested_model, suggested_reasoning = classify(prompt.strip())
        recommendation = f"Recommended: {suggested_model} · {suggested_reasoning}"
        matches = model == suggested_model
        differs = bool(model) and not matches
        if not claim_session(session_id):
            return hook_output()
        if not differs and matches:
            return hook_output(system_message=(
                f"{recommendation}\n\nYour selected model matches this recommendation."
            ))
        if not differs:
            return hook_output(system_message=recommendation)

        try:
            answer = await ctx.elicit(
                message=(
                    f"Recommendation: {suggested_model} · {suggested_reasoning}\n\n"
                    "What would you like to do?"
                ),
                schema=PromptChoice,
            )
        except Exception:
            marker.unlink(missing_ok=True)
            raise
        if (
            answer.action == "accept"
            and answer.data.action == "Use my current settings for this prompt."
        ):
            return hook_output(system_message="Continuing with current settings.")
        return hook_output(
            decision="block",
            reason=(
                "Prompt paused. Change your settings, then send a short follow-up to continue "
                "with the original request."
            ),
            additional_context=(
                "The following is the user's original request, preserved as a JSON string "
                "because the prompt was blocked while they changed settings. Continue with "
                "this request when they resume; if they send an updated request, follow that.\n"
                f"{json.dumps(prompt.strip(), ensure_ascii=False)}"
            ),
        )
    except Exception as error:
        logging.exception("Model recommendation failed")
        return hook_output(system_message=(
            f"Model recommendation failed: {type(error).__name__}: {error}. "
            "The task will continue using the current settings. Rerun setup if a dependency "
            "or classifier checkpoint is missing."
        ))


if __name__ == "__main__":
    mcp.run(transport="stdio")
