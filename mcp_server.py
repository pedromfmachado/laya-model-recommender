"""Local MCP tool used by the synchronous prompt-submit hook."""
import json
import logging

from pydantic import BaseModel, ConfigDict, Field
from mcp.server.fastmcp import FastMCP, Context

from hook import (
    active_thread_settings,
    claim_thread,
    classify,
    clear_pending_request,
    compare_model,
    compare_reasoning,
    continuation_prompt,
    format_recommendation,
    read_pending_request,
    save_pending_request,
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
    """Recommend a model on the first prompt and check reasoning on every prompt."""
    thread_id = None
    state_thread_id = None
    try:
        if not prompt.strip() or not session_id.strip():
            raise ValueError("Missing prompt or session id")
        first_session_prompt = claim_thread(session_id)
        thread_id, current_reasoning = active_thread_settings(session_id, model)
        state_thread_id = thread_id or session_id
        # Model eligibility comes from the first prompt marker; resolving active
        # reasoning is only needed to decide whether a reasoning change applies.
        first_prompt = first_session_prompt
        pending_request = read_pending_request(state_thread_id)
        if pending_request and continuation_prompt(prompt):
            request_to_classify = pending_request
        else:
            request_to_classify = prompt.strip()
            if pending_request:
                clear_pending_request(state_thread_id)
        config, model_tier, reasoning_level = classify(
            request_to_classify, include_model=first_prompt
        )
        suggested_model = None
        if first_prompt:
            recommendation, suggested_model, suggested_reasoning = format_recommendation(
                config, model_tier, reasoning_level
            )
            model_differs, model_matches = compare_model({"model": model}, suggested_model)
        else:
            model_differs, model_matches = False, False
            suggested_reasoning = config["reasoning_mapping"][reasoning_level]
            recommendation = None

        reasoning_differs = compare_reasoning(current_reasoning, suggested_reasoning)
        if first_prompt and model_differs:
            prompt_recommendation = f"Recommendation: {suggested_model} · {suggested_reasoning}"
        elif first_prompt:
            prompt_recommendation = recommendation
        else:
            prompt_recommendation = f"Recommended reasoning: {suggested_reasoning}"

        if not (first_prompt and model_differs) and reasoning_differs is not True:
            if thread_id:
                clear_pending_request(thread_id)
            if first_prompt and model_matches:
                return hook_output(system_message=(
                    f"{recommendation}\n\nYour selected model matches this recommendation."
                ))
            if first_prompt:
                return hook_output(system_message=recommendation)
            return hook_output()

        answer = await ctx.elicit(
            message=(
                f"{prompt_recommendation}\n\n"
                "What would you like to do?"
            ),
            schema=PromptChoice,
        )
        if (
            answer.action == "accept"
            and answer.data.action == "Use my current settings for this prompt."
        ):
            clear_pending_request(state_thread_id)
            return hook_output(system_message="Continuing with current settings.")
        save_pending_request(state_thread_id, request_to_classify)
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
                f"{json.dumps(request_to_classify, ensure_ascii=False)}"
            ),
        )
    except Exception as error:
        if state_thread_id:
            try:
                clear_pending_request(state_thread_id)
            except OSError:
                logging.exception("Failed to clear pending request")
        logging.exception("Model recommendation failed")
        return hook_output(system_message=(
            f"Model recommendation failed: {type(error).__name__}: {error}. "
            "The task will continue using the current settings. Rerun setup if a dependency "
            "or classifier checkpoint is missing."
        ))


if __name__ == "__main__":
    mcp.run(transport="stdio")
