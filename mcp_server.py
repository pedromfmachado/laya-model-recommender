"""Local MCP tool used by the synchronous prompt-submit hook."""
import json
import logging

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP, Context

from hook import (
    claim_session,
    classify,
    compare_model,
    format_recommendation,
    session_marker,
)

logging.basicConfig(level=logging.WARNING)
mcp = FastMCP("model-recommender")


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

        config, capability, effort = classify(prompt.strip())
        recommendation, suggested_model, suggested_reasoning = format_recommendation(
            config, capability, effort
        )
        differs, matches = compare_model({"model": model}, suggested_model)
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
