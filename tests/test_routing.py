import asyncio
import threading
import time
from dataclasses import replace

import pytest

from laya_router.classifier import LayaClassifier
from laya_router.config import load_settings
from laya_router.routing import Router, user_texts


class FakeClassifier:
    ready = True
    mode = "test"

    def __init__(self, output=None):
        self.output = output or {"category": "coding", "confidence": .9}
        self.calls = []

    async def start(self, settings):
        pass

    async def close(self):
        pass

    async def classify(self, history, settings):
        self.calls.append(history)
        await asyncio.sleep(.001)
        if isinstance(self.output, Exception):
            raise self.output
        return self.output


@pytest.fixture
def settings():
    return load_settings("routes.example.toml")


@pytest.mark.parametrize("category,model,effort", [
    ("coding", "gpt-5.6-sol", "medium"), ("debugging", "gpt-5.6-sol", "high"),
    ("research", "gpt-5.6-terra", "high"), ("writing", "gpt-5.6-luna", "low"),
    ("general", "gpt-5.6-luna", "medium"),
])
async def test_category_routes(settings, category, model, effort):
    router = Router(settings, FakeClassifier({"category": category, "confidence": .9}))
    turn = await router.route({"input": "a task"})
    assert (turn.decision.route.model, turn.decision.route.effort) == (model, effort)


@pytest.mark.parametrize("output,reason", [
    ({"category": "coding", "confidence": .69}, "low_confidence"),
    ({"category": "other", "confidence": .99}, "invalid_classifier_output"),
    ({"category": "coding", "confidence": float("nan")}, "invalid_classifier_output"),
    ({"category": "coding", "confidence": True}, "invalid_classifier_output"),
    (RuntimeError("private text"), "classifier_failure"),
])
async def test_fallback(settings, output, reason):
    turn = await Router(settings, FakeClassifier(output)).route({"input": "secret"})
    assert turn.decision.route.category == "general"
    assert turn.decision.fallback == reason


async def test_one_classification_per_turn_and_new_identical_delta(settings):
    classifier = FakeClassifier()
    router = Router(settings, classifier)
    user = {"role": "user", "content": "Implement it"}
    body = {"input": [user], "conversation": "thread-a"}
    turns = await asyncio.gather(*[router.route(body) for _ in range(5)])
    assert len(classifier.calls) == 1
    router.remember("resp1", turns[0])
    full = await router.route({**body, "input": [user, {"type": "function_call_output", "output": "secret"}]})
    continuation = await router.route({"previous_response_id": "resp1", "input": [{"type": "function_call_output"}]})
    assert full == continuation == turns[0]
    await router.route({**body, "previous_response_id": "resp1"})
    assert len(classifier.calls) == 2
    assert classifier.calls[-1] == ["Implement it", "Implement it"]
    await router.route({"input": [user, {"role": "user", "content": "Now explain it"}], "conversation": "thread-a"})
    assert len(classifier.calls) == 3


async def test_conversation_isolation_and_unknown_continuation(settings):
    classifier = FakeClassifier()
    router = Router(settings, classifier)
    a, b = await asyncio.gather(router.route({"conversation": "a", "input": "same"}),
                                router.route({"conversation": "b", "input": "same"}))
    assert len(classifier.calls) == 2
    router.remember("response-a", a)
    for previous, identity in [("response-a", "b"), ("unknown", "a")]:
        turn = await router.route({"previous_response_id": previous, "conversation": identity, "input": []})
        assert turn.decision.route.category == "general"


def test_extraction_excludes_injected_content():
    body = {"input": [
        {"role": "system", "content": "secret system"},
        {"role": "assistant", "content": "assistant output"},
        {"role": "user", "content": "# AGENTS.md instructions\n<INSTRUCTIONS>secret agent</INSTRUCTIONS>\n<environment_context>secret env</environment_context>"},
        {"role": "user", "content": "injected", "internal_chat_message_metadata_passthrough": {"content_item_kinds": ["environments.environment_context"]}},
        {"type": "function_call_output", "output": "secret tool"},
        {"role": "user", "content": [{"type": "input_image", "image_url": "secret image"}, {"type": "input_text", "text": "  real   request "}]},
    ]}
    assert user_texts(body) == ["real request"]


def test_missing_alias_uses_general(tmp_path):
    path = tmp_path / "routes.toml"
    path.write_text('[routes.general]\nmodel="one-model"\n')
    configured = load_settings(path)
    assert {route.model for route in configured.routes.values()} == {"one-model"}


async def test_canceled_inference_remains_serialized(settings):
    classifier = LayaClassifier()
    active = 0
    maximum = 0
    started = threading.Event()
    def predict(history, configured):
        nonlocal active, maximum
        active += 1
        maximum = max(maximum, active)
        started.set()
        time.sleep(.06)
        active -= 1
        return {"category": "coding", "confidence": .9}
    classifier._predict = predict
    first = asyncio.create_task(classifier.classify(["first"], settings))
    await asyncio.to_thread(started.wait)
    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    await classifier.classify(["second"], settings)
    await classifier.close()
    assert maximum == 1


async def test_checkpoint_budget_and_answer_confidence(settings):
    class Tokenizer:
        def __call__(self, text, **kwargs):
            return {"input_ids": list(text.encode())}
        def decode(self, ids, **kwargs):
            return bytes(ids).decode()
    class Agent:
        cfg = {"max_len": 512, "head_max_len": 192}
        tok = Tokenizer()
        def predict(self, state, **kwargs):
            assert len(state) < 512
            assert "Latest user request: latest" in state
            assert kwargs["max_len"] == 512
            return {"answers": {"category": {"choice": "coding", "confidence": .1, "answer_confidence": .91}}}
    classifier = LayaClassifier()
    classifier.agent = Agent()
    classifier.ready = True
    result = await classifier.classify(["previous" * 10000, "latest" * 10000], settings)
    await classifier.close()
    assert result["confidence"] == .91


async def test_lru_bound(settings):
    router = Router(replace(settings, cache_size=2), FakeClassifier())
    for index in range(4):
        turn = await router.route({"input": str(index)})
        router.remember(str(index), turn)
    assert len(router.turns) == len(router.responses) == 2
