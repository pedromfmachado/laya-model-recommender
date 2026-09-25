"""Bounded, privacy-conscious turn extraction and route caching."""
import asyncio
from collections import OrderedDict
from dataclasses import dataclass
import hashlib
import json
import math
import re
import time

from .config import Settings, Route


BLOCKS = re.compile(
    r"<(environment_context|INSTRUCTIONS|system_reminder|system-reminder|developer_instructions|"
    r"permissions instructions|collaboration_mode)\b[^>]*>.*?</\1>", re.S | re.I
)


def user_texts(body: dict) -> list[str]:
    value = body.get("input", [])
    if isinstance(value, str):
        value = [{"role": "user", "content": value}]
    result = []
    if not isinstance(value, list):
        return result
    for item in value:
        if not isinstance(item, dict) or item.get("role") != "user":
            continue
        metadata = item.get("internal_chat_message_metadata_passthrough", {})
        kinds = metadata.get("content_item_kinds", []) if isinstance(metadata, dict) else []
        if kinds and all(kind.startswith(("environments.", "instructions.", "permissions.")) for kind in kinds if isinstance(kind, str)):
            continue
        content = item.get("content", [])
        if isinstance(content, str):
            text = content
        elif isinstance(content, list):
            text = "\n".join(part.get("text", "") for part in content
                             if isinstance(part, dict) and part.get("type") in {"input_text", "text"}
                             and isinstance(part.get("text"), str))
        else:
            continue
        # Codex emits AGENTS content in an INSTRUCTIONS envelope. A bare AGENTS
        # message has no trustworthy user-request boundary, so exclude that whole item.
        if text.lstrip().startswith("# AGENTS.md instructions") and "<INSTRUCTIONS>" not in text:
            continue
        text = BLOCKS.sub("", text)
        text = re.sub(r"(?m)^# AGENTS\.md instructions\s*$", "", text)
        text = " ".join(text.split())
        if text:
            result.append(text)
    return result


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


@dataclass(frozen=True)
class Decision:
    route: Route
    duration_ms: float = 0
    fallback: str | None = None
    confidence: float | None = None
    predicted_category: str | None = None


@dataclass(frozen=True)
class Turn:
    decision: Decision
    history: tuple[str, ...]
    identity: str


class Router:
    def __init__(self, settings: Settings, classifier):
        self.settings = settings
        self.classifier = classifier
        self.turns: OrderedDict[str, Turn] = OrderedDict()
        self.responses: OrderedDict[str, Turn] = OrderedDict()
        self.pending: dict[str, asyncio.Task] = {}
        self.lock = asyncio.Lock()

    def _put(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > self.settings.cache_size:
            cache.popitem(last=False)

    def remember(self, response_id: str, turn: Turn):
        if response_id:
            self._put(self.responses, response_id, turn)

    async def _classify(self, history) -> Decision:
        start = time.monotonic()
        fallback = None
        confidence = None
        predicted = None
        category = "general"
        try:
            # The classifier owns its tokenizer and serialized inference worker.
            raw = await self.classifier.classify(list(history[-2:]), self.settings)
            proposed = raw.get("category")
            confidence = raw.get("confidence")
            if proposed not in self.settings.routes or isinstance(confidence, bool) or not isinstance(confidence, (int, float)) or not math.isfinite(confidence) or not 0 <= confidence <= 1:
                fallback = "invalid_classifier_output"
            elif confidence < self.settings.threshold:
                fallback = "low_confidence"
            else:
                category = proposed
            if proposed in self.settings.routes:
                predicted = proposed
        except Exception:
            fallback = "classifier_unavailable" if not self.classifier.ready else "classifier_failure"
        return Decision(self.settings.routes[category], (time.monotonic() - start) * 1000, fallback, confidence, predicted)

    async def route(self, body: dict, headers=None) -> Turn:
        headers = headers or {}
        conversation = body.get("conversation")
        if isinstance(conversation, dict):
            conversation = conversation.get("id")
        identity = str(conversation or headers.get("session_id") or headers.get("x-codex-thread-id")
                       or body.get("prompt_cache_key") or "anonymous")
        previous_id = body.get("previous_response_id")
        previous = self.responses.get(previous_id) if isinstance(previous_id, str) else None
        # A caller with an explicit different identity cannot borrow another conversation's route.
        if previous and identity != "anonymous" and previous.identity != identity:
            previous = None
        texts = tuple(user_texts(body))
        if not texts:
            if previous:
                return previous
            return Turn(Decision(self.settings.routes["general"], fallback="no_user_text"), (), identity)
        # previous_response_id + user input means a new turn, even when its text is identical.
        history = (previous.history + texts) if previous else texts
        key = digest([identity, previous_id, history])
        async with self.lock:
            cached = self.turns.get(key)
            if cached:
                self.turns.move_to_end(key)
                return cached
            task = self.pending.get(key)
            if task is None:
                async def classify_and_save():
                    try:
                        decision = await self._classify(history)
                        turn = Turn(decision, tuple(text[:65536] for text in history[-2:]),
                                    previous.identity if previous and identity == "anonymous" else identity)
                        self._put(self.turns, key, turn)
                        return turn
                    finally:
                        self.pending.pop(key, None)
                task = asyncio.create_task(classify_and_save())
                self.pending[key] = task
        # Canceling one HTTP caller must not cancel inference shared by another caller.
        return await asyncio.shield(task)
