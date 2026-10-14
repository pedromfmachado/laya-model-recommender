"""Local MCP tool used by the synchronous prompt-submit hook."""
import logging

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP, Context

from hook import claim_session, classify, compare_model, format_recommendation, session_marker

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
                "Continue with current settings",
                "Cancel this prompt and change settings",
            ]
        },
    )


def hook_output(decision=None, reason=None, system_message=None):
    result = {}
    if decision:
        result["decision"] = decision
    if reason:
        result["reason"] = reason
    if system_message:
        result["systemMessage"] = system_message
    return result


@mcp.tool()
async def recommend_for_prompt(
    ctx: Context,
    prompt: str,
    session_id: str,
    model: str = "",
) -> dict:
    """Classify the first prompt locally and ask what to do when settings differ."""
    try:
        if not prompt.strip() or not session_id.strip():
            raise ValueError("Missing prompt or session id")
        if session_marker(session_id).exists():
            return hook_output()

        config, capability, effort = classify(prompt.strip())
        recommendation, suggested_model, _ = format_recommendation(
            config, capability, effort
        )
        differs, matches = compare_model({"model": model}, suggested_model)
        if not differs and matches:
            if not claim_session(session_id):
                return hook_output()
            return hook_output(system_message=(
                f"{recommendation}\n\nYour selected model matches this recommendation."
            ))

        if not differs:
            if not claim_session(session_id):
                return hook_output()
            return hook_output(system_message=recommendation)

        if not claim_session(session_id):
            return hook_output()

        marker = session_marker(session_id)
        try:
            answer = await ctx.elicit(
                message=(
                    f"{recommendation}\n\nThe recommended model differs from your current model."
                ),
                schema=PromptChoice,
            )
        except Exception:
            marker.unlink(missing_ok=True)
            raise
        if answer.action == "accept" and answer.data.action == "Continue with current settings":
            return hook_output(system_message="Continuing with current settings.")
        return hook_output(
            decision="block",
            reason="Prompt cancelled. Change your settings, then resubmit.",
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
