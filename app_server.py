"""Read current thread settings from the Codex app server."""
import asyncio
import json


async def current_reasoning(session_id):
    async def lookup():
        process = await asyncio.create_subprocess_exec(
            "codex", "app-server", "proxy",
            stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            async def send(message):
                process.stdin.write((json.dumps(message) + "\n").encode())
                await process.stdin.drain()

            async def response(request_id):
                while line := await process.stdout.readline():
                    message = json.loads(line)
                    if message.get("id") == request_id:
                        return message.get("result", {})
                return {}

            await send({"id": 1, "method": "initialize", "params": {
                "clientInfo": {"name": "model-recommender", "version": "1"},
            }})
            if not await response(1):
                return None
            await send({"method": "initialized"})
            await send({"id": 2, "method": "thread/read", "params": {"threadId": session_id}})
            thread = (await response(2)).get("thread", {})
            if thread.get("status", {}).get("type") not in ("active", "idle"):
                return None
            effort = thread.get("reasoningEffort")
            return effort if isinstance(effort, str) and effort else None
        finally:
            if process.returncode is None:
                process.kill()
            await process.wait()

    try:
        return await asyncio.wait_for(lookup(), timeout=2)
    except (OSError, ValueError, TypeError, asyncio.TimeoutError):
        return None


