import asyncio
import builtins
import copy
import threading
import time

import pytest

from custom_classifier import LayaClassifier, latest_user_text


def test_classifier_singleton_survives_litellm_model_config_deepcopy():
    assert copy.deepcopy(LayaClassifier) is LayaClassifier


def test_preload_finishes_before_classifier_is_available(monkeypatch):
    loaded_on = []
    agent = object()

    def load_agent(self):
        loaded_on.append(threading.current_thread().name)
        self._agent = agent

    monkeypatch.setattr(LayaClassifier.__class__, "_load_agent", load_agent)
    classifier = LayaClassifier.__class__(preload=True)
    try:
        assert classifier._agent is agent
        assert loaded_on == ["laya-classifier_0"]
        assert copy.deepcopy(classifier) is classifier
    finally:
        classifier._executor.shutdown(wait=True)


def test_preload_failure_stops_startup(monkeypatch):
    def fail_load(_self):
        raise OSError("checkpoint unavailable")

    monkeypatch.setattr(LayaClassifier.__class__, "_load_agent", fail_load)
    with pytest.raises(RuntimeError, match="failed to load during proxy startup"):
        LayaClassifier.__class__(preload=True)


def test_latest_user_text_skips_non_user_empty_and_non_text_content():
    context = {
        "structured_messages": [
            {"role": "user", "content": "older request"},
            {"role": "assistant", "content": "assistant response"},
            {"role": "tool", "content": "tool output"},
            {"role": "user", "content": [{"type": "image_url", "url": "hidden"}]},
            {"role": "user", "content": [{"type": "text", "text": "  newest ask  "}]},
        ]
    }
    assert latest_user_text(context) == "newest ask"


def test_latest_user_text_accepts_context_object_and_limits_size():
    class Context:
        raw_messages = [{"role": "user", "content": "x" * 70000}]

    assert latest_user_text(Context()) == "x" * 65536


@pytest.mark.parametrize(
    ("category", "expected"),
    [("simple", "SIMPLE"), ("medium", "MEDIUM"), ("complex", "COMPLEX"), ("reasoning", "REASONING")],
)
def test_predict_maps_categories(category, expected):
    classifier = LayaClassifier.__class__()

    class Agent:
        def predict(self, *_args, **_kwargs):
            return {"answers": {"category": {"choice": category}}}

    classifier._agent = Agent()
    try:
        assert classifier._predict("prompt") == expected
    finally:
        classifier._executor.shutdown(wait=True)


def test_empty_and_invalid_predictions():
    classifier = LayaClassifier.__class__()

    class Agent:
        def predict(self, *_args, **_kwargs):
            return {"answers": {"category": {"choice": "other"}}}

    classifier._agent = Agent()
    try:
        assert asyncio.run(classifier.classify({"structured_messages": [{"role": "user", "content": " "}]})) == "SIMPLE"
        assert classifier._predict("prompt") is None
    finally:
        classifier._executor.shutdown(wait=True)


def test_prediction_error_returns_none():
    classifier = LayaClassifier.__class__()

    class Agent:
        def predict(self, *_args, **_kwargs):
            raise RuntimeError("failure")

    classifier._agent = Agent()
    try:
        assert classifier._predict("prompt") is None
    finally:
        classifier._executor.shutdown(wait=True)


def test_model_load_error_returns_none(monkeypatch):
    classifier = LayaClassifier.__class__()
    real_import = builtins.__import__

    def fail_laya_import(name, *args, **kwargs):
        if name == "laya":
            raise ImportError("unavailable")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fail_laya_import)
    try:
        assert classifier._predict("prompt") is None
        assert classifier._load_error is True
    finally:
        classifier._executor.shutdown(wait=True)


def test_timeout_returns_none_and_worker_remains_serialized():
    classifier = LayaClassifier.__class__(timeout_seconds=0.01)
    started = threading.Event()
    active = 0
    max_active = 0
    lock = threading.Lock()

    def slow_predict(_prompt):
        nonlocal active, max_active
        with lock:
            active += 1
            max_active = max(max_active, active)
        started.set()
        time.sleep(0.05)
        with lock:
            active -= 1
        return "SIMPLE"

    classifier._predict = slow_predict

    async def run_two():
        first = asyncio.create_task(classifier.classify({"structured_messages": [{"role": "user", "content": "a"}]}))
        await asyncio.to_thread(started.wait)
        second = asyncio.create_task(classifier.classify({"structured_messages": [{"role": "user", "content": "b"}]}))
        return await asyncio.gather(first, second)

    try:
        assert asyncio.run(run_two()) == [None, None]
        assert max_active == 1
    finally:
        classifier._executor.shutdown(wait=True)


def test_timeout_keeps_capacity_until_underlying_work_finishes():
    classifier = LayaClassifier.__class__(timeout_seconds=0.02)
    started = threading.Event()
    release_worker = threading.Event()

    def blocked_predict(_prompt):
        started.set()
        release_worker.wait()
        return "SIMPLE"

    classifier._predict = blocked_predict

    async def exercise():
        first = asyncio.create_task(classifier.classify({"structured_messages": [{"role": "user", "content": "first"}]}))
        await asyncio.to_thread(started.wait)
        assert await first is None  # The caller times out while the worker remains blocked.
        assert await classifier.classify({"structured_messages": [{"role": "user", "content": "overload"}]}) is None
        release_worker.set()
        for _ in range(100):
            if classifier._capacity.acquire(blocking=False):
                classifier._capacity.release()
                break
            await asyncio.sleep(0.005)
        else:
            raise AssertionError("worker completion did not release classifier capacity")
        return await classifier.classify({"structured_messages": [{"role": "user", "content": "after completion"}]})

    try:
        assert asyncio.run(exercise()) == "SIMPLE"
    finally:
        release_worker.set()
        classifier._executor.shutdown(wait=True)
