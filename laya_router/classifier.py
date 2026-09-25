import asyncio
from concurrent.futures import ThreadPoolExecutor
import logging
import re

from .config import Settings

LOG = logging.getLogger("laya_router")


class LayaClassifier:
    """One dedicated worker serializes checkpoint loading, tokenization, and inference.

    A canceled asyncio waiter cannot release the worker while inference is still running.
    """
    def __init__(self):
        self.worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="laya")
        self.agent = None
        self.ready = False
        self.mode = "laya"

    async def start(self, settings: Settings):
        def load():
            import laya
            self.agent = laya.load(settings.checkpoint)
        try:
            await asyncio.get_running_loop().run_in_executor(self.worker, load)
            self.ready = True
        except Exception:
            # Third-party errors may contain paths, URLs, or credentials; never log their text.
            LOG.warning("classifier_load_failed mode=general_fallback")
            self.mode = "fallback"

    def _predict(self, history, settings):
        if not self.ready:
            raise RuntimeError("Classifier unavailable")
        agent = self.agent
        limit = min(settings.token_budget, int(agent.cfg.get("max_len", 512)))
        head = min(int(agent.cfg.get("head_max_len", 192)), limit - 32)
        room = max(1, limit - head - 8)
        # Apply a character cap before tokenization, then allocate most state tokens to latest.
        def bounded(text, budget):
            text = text[:65536]
            ids = agent.tok(text, add_special_tokens=False)["input_ids"]
            return agent.tok.decode(ids[:budget], skip_special_tokens=True)
        latest_budget = room if len(history) == 1 else max(1, room * 3 // 4)
        state = "Latest user request: " + bounded(history[-1], latest_budget)
        if len(history) > 1:
            state += "\nPreceding user request: " + bounded(history[-2], room - latest_budget)
        questions = {"category": {
            "type": "choice",
            "instructions": "Classify the latest user request by its primary task. Use preceding context only to interpret follow-ups.",
            "criteria": {name: route.description for name, route in settings.routes.items()},
        }}
        output = agent.predict(state, questions=questions, max_len=limit, head_max_len=head)
        answer = output["answers"]["category"]
        return {"category": answer["choice"], "confidence": answer["answer_confidence"]}

    async def classify(self, history, settings):
        return await asyncio.get_running_loop().run_in_executor(self.worker, self._predict, history, settings)

    async def close(self):
        await asyncio.to_thread(self.worker.shutdown, wait=True, cancel_futures=True)


class DemoClassifier:
    """Deterministic fixture classifier, explicitly not Laya or a quality evaluation."""
    ready = True
    mode = "demo"

    async def start(self, settings):
        pass

    async def close(self):
        pass

    async def classify(self, history, settings):
        text = history[-1].lower()
        rules = [
            ("debugging", r"\b(debug|bug|fail\w*|crash\w*|fix|defect)\b"),
            ("research", r"\b(research|compare|investigate|review|plan|architecture)\b"),
            ("writing", r"\b(document\w*|explain|prose|readme|summarize|rewrite)\b"),
            ("coding", r"\b(implement|code|function|refactor|test\w*|build)\b"),
        ]
        return {"category": next((category for category, pattern in rules if re.search(pattern, text)), "general"),
                "confidence": 1.0}
