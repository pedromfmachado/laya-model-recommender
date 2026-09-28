"""Message extraction and validated two-level classification with Laya."""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from concurrent.futures import ThreadPoolExecutor
import logging
import os
from typing import Any

logger = logging.getLogger("LiteLLM_Laya_Classifier")

TASK_TYPES = frozenset({"general", "coding", "debugging", "research", "writing"})
COMPLEXITIES = frozenset({"simple", "medium", "complex"})


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _text_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    return "\n".join(
        block["text"].strip()
        for block in content
        if isinstance(block, Mapping)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
        and block["text"].strip()
    )


def latest_user_text(context: Any) -> str:
    """Return the newest nonempty human message from LiteLLM's routing context."""
    messages = _value(context, "structured_messages") or _value(context, "raw_messages", [])
    if not isinstance(messages, list):
        return ""
    return next(
        (
            text[-65536:]
            for message in reversed(messages)
            if _value(message, "role") == "user"
            if (text := _text_content(_value(message, "content")))
        ),
        "",
    )


class LayaClassifier:
    """Load Laya once and serialize inference off LiteLLM's event loop."""

    def __init__(self, timeout_seconds: float = 7.5, preload: bool = False) -> None:
        self.timeout_seconds = timeout_seconds
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya-classifier")
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
        memo[id(self)] = self
        return self

    def _load_agent(self) -> None:
        import laya

        self._agent = laya.load("convaiinnovations/laya")

    def _predict(self, prompt: str) -> tuple[str, str] | None:
        if self._agent is None:
            if self._load_error:
                return None
            try:
                self._load_agent()
            except Exception:
                self._load_error = True
                logger.warning("classifier_load_failed fallback=general/simple")
                return None
        try:
            result = self._agent.predict(
                prompt,
                questions={
                    "task_type": {
                        "type": "choice",
                        "instructions": "Classify the main kind of work requested.",
                        "criteria": {
                            "GENERAL": "Questions, explanations, analysis, or tasks not primarily coding, debugging, research, or writing.",
                            "CODING": "Creating, changing, or explaining software code.",
                            "DEBUGGING": "Finding or fixing a defect, failure, or unexpected behavior.",
                            "RESEARCH": "Gathering, comparing, or synthesizing information from sources.",
                            "WRITING": "Drafting, editing, or transforming prose or other written content.",
                        },
                    },
                    "complexity": {
                        "type": "choice",
                        "instructions": "Classify the effort needed to complete the request well.",
                        "criteria": {
                            "SIMPLE": "One-step request, small transformation, or direct factual answer.",
                            "MEDIUM": "Several bounded steps, moderate analysis, or a contained implementation.",
                            "COMPLEX": "Sustained reasoning, multiple dependent steps, broad implementation, or difficult investigation.",
                        },
                    },
                },
            )
            answers = result["answers"]
            task_type = answers["task_type"]["choice"].strip().lower()
            complexity = answers["complexity"]["choice"].strip().lower()
            if task_type in TASK_TYPES and complexity in COMPLEXITIES:
                return task_type, complexity
            logger.warning("classifier_invalid_result fallback=general/simple")
        except Exception:
            logger.warning("classifier_inference_failed fallback=general/simple")
        return None

    async def classify(self, context: Any) -> tuple[str, str] | None:
        prompt = latest_user_text(context)
        if not prompt:
            return "general", "simple"
        try:
            worker_future = self._executor.submit(self._predict, prompt)
        except Exception:
            logger.warning("classifier_submit_failed fallback=general/simple")
            return None
        try:
            return await asyncio.wait_for(asyncio.wrap_future(worker_future), timeout=self.timeout_seconds)
        except asyncio.TimeoutError:
            logger.warning("classifier_timeout fallback=general/simple")
            return None


laya_classifier = LayaClassifier(preload=os.getenv("LAYA_PRELOAD_ON_STARTUP") == "1")
