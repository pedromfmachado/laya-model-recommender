"""In-process Laya classifier plugin for the LiteLLM complexity router."""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
from collections.abc import Mapping
import os
import threading
from typing import Any

logger = logging.getLogger("LiteLLM_Laya_Classifier")


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts = [
        block.get("text", "")
        for block in content
        if isinstance(block, Mapping) and block.get("type") == "text" and isinstance(block.get("text"), str)
    ]
    return "\n".join(part.strip() for part in parts if part.strip())


def latest_user_text(context: Any) -> str:
    """Return the newest nonempty human message from LiteLLM's routing context."""
    messages = _value(context, "structured_messages")
    if not isinstance(messages, list):
        messages = _value(context, "raw_messages", [])
    if not isinstance(messages, list):
        return ""
    for message in reversed(messages):
        role = _value(message, "role")
        if role != "user":
            continue
        content = _text_content(_value(message, "content"))
        if content:
            return content[-65536:]
    return ""


class LayaClassifier:
    """Load Laya before serving requests and serialize inference off the event loop."""

    def __init__(self, timeout_seconds: float = 7.5, preload: bool = False) -> None:
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya-classifier")
        self._capacity = threading.BoundedSemaphore(1)
        self._agent: Any = None
        self._load_error = False
        if preload:
            try:
                self._executor.submit(self._load_agent).result()
            except Exception:
                self._executor.shutdown(wait=True)
                logger.exception("classifier_preload_failed")
                raise RuntimeError("Laya classifier failed to load during proxy startup") from None

    def __deepcopy__(self, memo: dict[int, Any]) -> LayaClassifier:
        """Keep this shared plugin intact when LiteLLM copies its model config."""
        memo[id(self)] = self
        return self

    def _load_agent(self) -> None:
        import laya

        self._agent = laya.load("convaiinnovations/laya")

    def _predict(self, prompt: str) -> str | None:
        if self._agent is None:
            if self._load_error:
                return None
            try:
                self._load_agent()
            except Exception:
                self._load_error = True
                logger.warning("classifier_load_failed fallback=default_model")
                return None
        try:
            result = self._agent.predict(
                prompt,
                questions={
                    "category": {
                        "type": "choice",
                        "instructions": "Classify the user's request by the work it requires. Use the lowest tier that can do the work well.",
                        "criteria": {
                            "SIMPLE": "A straightforward factual question, short explanation, or small transformation. No substantial analysis or multi-step work is needed.",
                            "MEDIUM": "A bounded coding task or practical request with a few clear steps, limited changes, or routine debugging.",
                            "COMPLEX": "A substantial software task involving multiple components, broad changes, difficult debugging, or several dependent steps.",
                            "REASONING": "A request dominated by deep analysis, formal or multi-stage reasoning, careful tradeoffs, or a difficult problem whose solution needs sustained deliberation.",
                        },
                    }
                },
            )
            category = result["answers"]["category"]["choice"]
            if not isinstance(category, str):
                return None
            category = category.strip().lower()
            if category.upper() in {"SIMPLE", "MEDIUM", "COMPLEX", "REASONING"}:
                return category.upper()
            logger.warning("classifier_invalid_category fallback=default_model")
            return None
        except Exception:
            logger.warning("classifier_inference_failed fallback=default_model")
            return None

    async def classify(self, context: Any) -> str | None:
        prompt = latest_user_text(context)
        if not prompt:
            return "SIMPLE"
        if not self._capacity.acquire(blocking=False):
            logger.warning("classifier_overloaded fallback=default_model")
            return None
        try:
            worker_future = self._executor.submit(self._predict, prompt)
        except Exception:
            self._capacity.release()
            logger.warning("classifier_submit_failed fallback=default_model")
            return None
        worker_future.add_done_callback(lambda _future: self._capacity.release())
        future = asyncio.wrap_future(worker_future)
        try:
            return await asyncio.wait_for(future, timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("classifier_timeout fallback=default_model")
            return None


# LiteLLM resolves the dotted plugin path to an object with an async classify method.
LayaClassifier = LayaClassifier(preload=os.getenv("LAYA_PRELOAD_ON_STARTUP") == "1")
