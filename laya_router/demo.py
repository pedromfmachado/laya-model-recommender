"""An honest offline Responses fixture; it never purports to answer arbitrary tasks."""
import json
import time
import uuid

import httpx


def response_for(body):
    response_id = "resp_demo_" + uuid.uuid4().hex
    inputs = body.get("input", [])
    if isinstance(inputs, str):
        inputs = [{"role": "user", "content": inputs}]
    tool_result = next((item for item in reversed(inputs)
                        if isinstance(item, dict) and item.get("type") == "function_call_output"), None)
    demo_tool = next((tool for tool in body.get("tools", []) if tool.get("type") == "function"
                      and tool.get("name") == "demo_echo"), None)
    if demo_tool and not tool_result:
        output = [{"type": "function_call", "id": "fc_" + uuid.uuid4().hex,
                   "call_id": "call_" + uuid.uuid4().hex, "name": "demo_echo",
                   "arguments": json.dumps({"text": "offline round trip"}), "status": "completed"}]
    else:
        if tool_result:
            text = "[DEMO — no model inference] Offline demo received tool output: " + str(tool_result.get("output", ""))
        else:
            text = "[DEMO — no model inference] Offline demo is working. This is a deterministic Responses fixture; connect LiteLLM for model-generated answers."
        output = [{"type": "message", "id": "msg_" + uuid.uuid4().hex,
                   "role": "assistant", "status": "completed",
                   "content": [{"type": "output_text", "text": text, "annotations": []}]}]
    return {"id": response_id, "object": "response", "created_at": int(time.time()),
            "status": "completed", "model": body["model"], "output": output,
            "error": None, "incomplete_details": None, "parallel_tool_calls": False,
            "tool_choice": body.get("tool_choice", "auto"), "tools": body.get("tools", []),
            "usage": {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0,
                      "input_tokens_details": {"cached_tokens": 0}, "output_tokens_details": {"reasoning_tokens": 0}}}


def events_for(response):
    initial = {**response, "status": "in_progress", "output": [], "usage": None}
    yield {"type": "response.created", "response": initial}
    yield {"type": "response.in_progress", "response": initial}
    for index, item in enumerate(response["output"]):
        added = {**item, "status": "in_progress"}
        added["arguments" if item["type"] == "function_call" else "content"] = "" if item["type"] == "function_call" else []
        yield {"type": "response.output_item.added", "output_index": index, "item": added}
        common = {"item_id": item["id"], "output_index": index}
        if item["type"] == "function_call":
            yield {"type": "response.function_call_arguments.delta", **common, "delta": item["arguments"]}
            yield {"type": "response.function_call_arguments.done", **common, "arguments": item["arguments"]}
        else:
            part = item["content"][0]
            yield {"type": "response.content_part.added", **common, "content_index": 0,
                   "part": {**part, "text": ""}}
            yield {"type": "response.output_text.delta", **common, "content_index": 0, "delta": part["text"]}
            yield {"type": "response.output_text.done", **common, "content_index": 0, "text": part["text"]}
            yield {"type": "response.content_part.done", **common, "content_index": 0, "part": part}
        yield {"type": "response.output_item.done", "output_index": index, "item": item}
    yield {"type": "response.completed", "response": response}


async def demo_upstream(request):
    body = json.loads(request.content)
    response = response_for(body)
    if body.get("stream"):
        content = "".join(f"event: {event['type']}\ndata: {json.dumps({**event, 'sequence_number': index})}\n\n"
                          for index, event in enumerate(events_for(response)))
        return httpx.Response(200, text=content, headers={"content-type": "text/event-stream"})
    return httpx.Response(200, json=response)
