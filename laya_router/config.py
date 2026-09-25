from dataclasses import dataclass
from pathlib import Path
import math
import tomllib


CATEGORIES = {
    "coding": ("Implement features, refactor code, write tests", "medium"),
    "debugging": ("Diagnose failures and correct defects", "high"),
    "research": ("Investigate, compare, design architecture, plan, or review", "high"),
    "writing": ("Write documentation, explanations, or edit prose", "low"),
    "general": ("Other tasks, conversation, and fallback", "medium"),
}
EFFORTS = {"none", "minimal", "low", "medium", "high", "xhigh", "max", "ultra"}


@dataclass(frozen=True)
class Route:
    category: str
    model: str
    effort: str
    description: str


@dataclass(frozen=True)
class Settings:
    routes: dict[str, Route]
    checkpoint: str = "convaiinnovations/laya"
    threshold: float = 0.70
    token_budget: int = 2048
    cache_size: int = 2048
    models: dict | None = None


def load_settings(path: str | Path) -> Settings:
    with open(path, "rb") as file:
        data = tomllib.load(file)
    configured = data.get("routes", {})
    if set(configured) - CATEGORIES.keys():
        raise ValueError("Unknown routing category")
    general = configured.get("general", {}).get("model")
    if not isinstance(general, str) or not general.strip():
        raise ValueError("routes.general.model is required")
    routes = {}
    for category, (description, effort) in CATEGORIES.items():
        raw = configured.get(category, {})
        model = raw.get("model", general)
        effort = raw.get("effort", effort)
        description = raw.get("description", description)
        if not isinstance(model, str) or not model.strip() or effort not in EFFORTS:
            raise ValueError(f"Invalid route for {category}")
        if not isinstance(description, str) or not description.strip():
            raise ValueError(f"Invalid description for {category}")
        routes[category] = Route(category, model, effort, description)
    classifier = data.get("classifier", {})
    threshold = float(classifier.get("confidence_threshold", 0.70))
    budget = int(classifier.get("token_budget", 2048))
    cache_size = int(data.get("cache_size", 2048))
    if not math.isfinite(threshold) or not 0 <= threshold <= 1:
        raise ValueError("confidence_threshold must be between 0 and 1")
    if budget < 128 or cache_size < 1:
        raise ValueError("token_budget must be >=128 and cache_size must be positive")
    return Settings(routes, classifier.get("checkpoint", "convaiinnovations/laya"),
                    threshold, budget, cache_size, data.get("models", {}))

