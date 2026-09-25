"""CLI entry point for the local Laya Responses proxy."""
import argparse
import asyncio
import json
import logging
import os
from pathlib import Path

import httpx
import uvicorn

from laya_router.classifier import DemoClassifier, LayaClassifier
from laya_router.config import load_settings
from laya_router.demo import demo_upstream
from laya_router.profile import install_profile
from laya_router.proxy import DEFAULT_UPSTREAM, create_app
from laya_router.routing import Router


async def evaluate(settings, dataset, demo=False):
    classifier = DemoClassifier() if demo else LayaClassifier()
    await classifier.start(settings)
    rows = [json.loads(line) for line in Path(dataset).read_text().splitlines() if line.strip()]
    router = Router(settings, classifier)
    outcomes = []
    try:
        for row in rows:
            turn = await router.route({"input": [{"role": "user", "content": text} for text in row["history"]],
                                       "conversation": row["id"]})
            outcomes.append({"id": row["id"], "expected": row["category"], "actual": turn.decision.route.category,
                             "predicted_category": turn.decision.predicted_category,
                             "confidence": turn.decision.confidence, "fallback": turn.decision.fallback})
    finally:
        await classifier.close()
    count = len(outcomes)
    per_category = {}
    for category in settings.routes:
        selected = [row for row in outcomes if row["expected"] == category]
        per_category[category] = {
            "samples": len(selected),
            "classifier_correct": sum(row["predicted_category"] == category for row in selected),
            "routing_correct": sum(row["actual"] == category for row in selected),
            "fallbacks": sum(row["fallback"] is not None for row in selected),
        }
    return {"mode": classifier.mode, "classifier_ready": classifier.ready, "samples": count,
            "per_category": per_category,
            "routing_accuracy": sum(row["expected"] == row["actual"] for row in outcomes) / count if count else None,
            "classifier_accuracy": sum(row["expected"] == row["predicted_category"] for row in outcomes) / count if count else None,
            "fallback_frequency": sum(row["fallback"] is not None for row in outcomes) / count if count else None,
            "outcomes": outcomes,
            "note": "Demo results measure deterministic fixtures, not Laya accuracy." if demo else
            "Small illustrative dataset; threshold remains provisional. Accuracy includes routed fallback outcomes."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("serve", "demo", "install-codex-profile", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--routes", required=name != "demo", default="routes.example.toml")
        if name in {"serve", "demo", "install-codex-profile"}:
            command.add_argument("--port", type=int, default=8787)
        if name == "serve":
            command.add_argument("--upstream-responses-url", default=DEFAULT_UPSTREAM,
                                 help="Complete Responses endpoint URL, used literally")
        elif name == "install-codex-profile":
            command.add_argument("--codex-home", default="~/.codex")
            command.add_argument("--template-model", default="gpt-5.6-sol")
            command.add_argument("--codex-bin", default="codex")
            command.add_argument("--catalog-source", help="Complete JSON exported by codex debug models --bundled")
            command.add_argument("--overwrite", action="store_true")
        elif name == "evaluate":
            command.add_argument("--dataset", default="evaluation/prompts.jsonl")
            command.add_argument("--demo", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    # HTTPX's INFO request logs expose endpoint URLs. Route logs contain only explicit safe fields.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    try:
        settings = load_settings(args.routes)
        if hasattr(args, "port") and not 1 <= args.port <= 65535:
            raise ValueError("port must be between 1 and 65535")
        if args.command == "install-codex-profile":
            paths = install_profile(settings, args.codex_home, args.template_model,
                                    catalog_source=args.catalog_source, codex_bin=args.codex_bin,
                                    port=args.port, overwrite=args.overwrite)
            print("\n".join(map(str, paths)))
        elif args.command == "evaluate":
            print(json.dumps(asyncio.run(evaluate(settings, args.dataset, args.demo)), indent=2))
        else:
            demo = args.command == "demo"
            app = create_app(settings, "http://offline-demo.invalid/v1/responses" if demo else args.upstream_responses_url,
                             classifier=DemoClassifier() if demo else None,
                             transport=httpx.MockTransport(demo_upstream) if demo else None,
                             api_key=None if demo else os.environ.get("LITELLM_API_KEY"))
            uvicorn.run(app, host="127.0.0.1", port=args.port, access_log=False)
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    main()
