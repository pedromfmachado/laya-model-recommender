"""LiteLLM routing plugin and per-deployment reasoning-effort callback."""

from __future__ import annotations

from collections.abc import Mapping
import logging
import os
from typing import Any

from litellm.integrations.custom_logger import CustomLogger

from .classification import LayaClassifier, laya_classifier

logger = logging.getLogger("LiteLLM_Laya_Router")

# Every task type/complexity pair explicitly chooses a model and effort.
ROUTES: dict[tuple[str, str], tuple[str, str]] = {
    ("general", "simple"): ("gpt-5.6-luna", "low"),
    ("general", "medium"): ("gpt-5.6-luna", "medium"),
    ("general", "complex"): ("gpt-5.6-terra", "high"),
    ("coding", "simple"): ("gpt-5.6-luna", "low"),
    ("coding", "medium"): ("gpt-5.6-terra", "medium"),
    ("coding", "complex"): ("gpt-5.6-sol", "high"),
    ("debugging", "simple"): ("gpt-5.6-luna", "low"),
    ("debugging", "medium"): ("gpt-5.6-terra", "medium"),
    ("debugging", "complex"): ("gpt-5.6-sol", "high"),
    ("research", "simple"): ("gpt-5.6-terra", "low"),
    ("research", "medium"): ("gpt-5.6-sol", "medium"),
    ("research", "complex"): ("gpt-5.6-sol", "high"),
    ("writing", "simple"): ("gpt-5.6-luna", "low"),
    ("writing", "medium"): ("gpt-5.6-terra", "medium"),
    ("writing", "complex"): ("gpt-5.6-sol", "high"),
}
AUTO_MODELS = frozenset({"gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"})


def _value(obj: Any, key: str, default: Any = None) -> Any:
    return obj.get(key, default) if isinstance(obj, Mapping) else getattr(obj, key, default)


def _set(obj: Any, key: str, value: Any) -> None:
    if isinstance(obj, dict):
        obj[key] = value
    else:
        setattr(obj, key, value)


class LayaRoutingPlugin:
    """Classify only the complete auto pool and narrow it to one deployment."""

    def __init__(self, classifier: LayaClassifier = laya_classifier) -> None:
        self.classifier = classifier

    async def run(self, context: Any) -> Any:
        candidates = _value(context, "candidate_models", [])
        if not isinstance(candidates, list) or len(candidates) != len(AUTO_MODELS) or set(candidates) != AUTO_MODELS:
            return context

        classification = await self.classifier.classify(context)
        task_type, complexity = classification or ("general", "simple")
        model, effort = ROUTES.get((task_type, complexity), ROUTES[("general", "simple")])
        _set(context, "candidate_models", [model])
        signals = _value(context, "signals")
        if not isinstance(signals, dict):
            signals = dict(signals) if isinstance(signals, Mapping) else {}
            _set(context, "signals", signals)
        signals["laya_task_type"] = task_type
        signals["laya_complexity"] = complexity
        signals["laya_reasoning_effort"] = effort
        return context


direct_plugin = LayaRoutingPlugin()


class ReasoningEffortCallback(CustomLogger):
    """Apply the plugin-selected effort to Chat Completions or Responses calls."""

    async def async_pre_call_deployment_hook(self, kwargs: dict[str, Any], call_type: str) -> dict[str, Any]:
        metadata = kwargs.get("metadata") or {}
        signals = metadata.get("routing_plugin_signals", {}) if isinstance(metadata, Mapping) else {}
        effort = signals.get("laya_reasoning_effort") if isinstance(signals, Mapping) else None
        if effort in {"low", "medium", "high"}:
            if "response" in call_type:
                reasoning = kwargs.get("reasoning")
                if not isinstance(reasoning, dict):
                    reasoning = {}
                    kwargs["reasoning"] = reasoning
                reasoning["effort"] = effort
            elif "completion" in call_type:
                kwargs["reasoning_effort"] = effort
        return kwargs


reasoning_effort_callback = ReasoningEffortCallback()
