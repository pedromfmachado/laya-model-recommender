"""Launch a real loopback demo server, verify JSON/SSE/tools/follow-up, then stop it."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

import httpx


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=18787)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    process = subprocess.Popen([sys.executable, str(root / "laya_proxy.py"), "demo", "--routes",
                                str(root / "routes.example.toml"), "--port", str(args.port)])
    try:
        with httpx.Client(base_url=f"http://127.0.0.1:{args.port}", timeout=5, trust_env=False) as client:
            for attempt in range(100):
                if process.poll() is not None:
                    raise RuntimeError("Demo server exited before becoming ready")
                try:
                    health = client.get("/healthz")
                    health.raise_for_status()
                    break
                except httpx.TransportError:
                    time.sleep(.05)
            else:
                raise RuntimeError("Demo startup timed out")
            assert health.json()["mode"] == "demo"
            body = {"model": "laya-auto", "input": "Implement a tool round trip"}
            response = client.post("/v1/responses", json=body)
            response.raise_for_status()
            assert "[DEMO — no model inference]" in response.json()["output"][0]["content"][0]["text"]
            tools = [{"type": "function", "name": "demo_echo", "description": "Echo a fixture string",
                      "parameters": {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}}]
            with client.stream("POST", "/v1/responses", json={**body, "stream": True, "tools": tools}) as stream:
                stream.raise_for_status()
                events = [json.loads(line[5:]) for line in stream.iter_lines() if line.startswith("data:")]
            completed = events[-1]["response"]
            assert events[-1]["type"] == "response.completed"
            call = completed["output"][0]
            assert call["type"] == "function_call" and call["name"] == "demo_echo"
            output = json.loads(call["arguments"])["text"]
            continuation = client.post("/v1/responses", json={"model": "laya-auto", "previous_response_id": completed["id"],
                "input": [{"type": "function_call_output", "call_id": call["call_id"], "output": output}]})
            continuation.raise_for_status()
            assert continuation.json()["model"] == "gpt-5.6-sol"
            assert output in continuation.json()["output"][0]["content"][0]["text"]
            followup = client.post("/v1/responses", json={"model": "laya-auto", "previous_response_id": continuation.json()["id"],
                "input": "Explain this in a README"})
            followup.raise_for_status()
            assert followup.json()["model"] == "gpt-5.6-luna"
            print("PASS: real HTTP health, JSON, SSE, tool round trip, stable continuation, and category-changing follow-up")
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()


if __name__ == "__main__":
    main()
