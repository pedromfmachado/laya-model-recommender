import copy
import json
from pathlib import Path
import subprocess


def install_profile(settings, directory, template_model="gpt-5.6-sol", *, catalog_source=None, codex_bin="codex", port=8787, overwrite=False):
    directory = Path(directory).expanduser().resolve()
    profile = directory / "laya.config.toml"
    catalog_path = directory / "laya.models.json"
    if not overwrite and (profile.exists() or catalog_path.exists()):
        raise ValueError("Generated files already exist; pass --overwrite to replace them")
    if catalog_source:
        source = json.loads(Path(catalog_source).read_text())
    else:
        try:
            exported = subprocess.run([codex_bin, "debug", "models", "--bundled"], check=True,
                                      capture_output=True, text=True, timeout=30)
        except (subprocess.SubprocessError, OSError):
            raise ValueError("Could not export model descriptors from the installed Codex binary") from None
        source = json.loads(exported.stdout)
    models = source["models"] if isinstance(source, dict) else source
    descriptors = {model["slug"]: model for model in models}
    if template_model not in descriptors:
        raise ValueError("Template model is absent from the installed Codex catalog")
    template = copy.deepcopy(descriptors[template_model])
    limits = []
    for alias in {route.model for route in settings.routes.values()}:
        metadata = (settings.models or {}).get(alias, {})
        canonical = metadata.get("canonical_model", "")
        if not canonical.startswith("gpt-") or canonical not in descriptors:
            raise ValueError(f"Model {alias} requires a canonical GPT identity in the exported catalog")
        limit = metadata.get("context_window")
        if isinstance(limit, bool) or not isinstance(limit, int) or limit <= 0:
            raise ValueError(f"Model {alias} requires a positive explicit context_window")
        if limit > descriptors[canonical].get("max_context_window", descriptors[canonical]["context_window"]):
            raise ValueError(f"Model {alias} context_window exceeds the canonical model limit")
        if not metadata.get("supports_tools"):
            raise ValueError(f"Model {alias} requires an explicit supports_tools=true declaration")
        if not set(template.get("input_modalities", ["text"])).issubset(metadata.get("input_modalities", [])):
            raise ValueError(f"Model {alias} lacks the template's input modalities")
        required_efforts = {route.effort for route in settings.routes.values() if route.model == alias}
        if not required_efforts.issubset(metadata.get("reasoning_efforts", [])):
            raise ValueError(f"Model {alias} lacks a configured reasoning effort")
        limits.append(limit)
    template.update(slug="laya-auto", display_name="Laya Auto", visibility="list", supported_in_api=True,
                    description="Category routing controls the model and reasoning effort; Codex effort selections are overridden.",
                    context_window=min(limits), max_context_window=min(limits),
                    use_responses_lite=False, supports_experimental_context=False,
                    supports_search_tool=False, experimental_supported_tools=[],
                    additional_speed_tiers=[], service_tiers=[], upgrade=None,
                    default_reasoning_level="medium",
                    supported_reasoning_levels=[{"effort": "medium", "description": "Automatic category effort"}])
    for field in ("tool_mode", "availability_nux", "web_search_tool_type"):
        template.pop(field, None)
    catalog = json.dumps({"models": [template]}, indent=2) + "\n"
    content = (f'model = "laya-auto"\nmodel_provider = "laya"\n'
               f'model_catalog_json = {json.dumps(str(catalog_path))}\n\n'
               '[model_providers.laya]\nname = "Laya Auto"\n'
               f'base_url = "http://127.0.0.1:{port}/v1"\nwire_api = "responses"\n'
               'requires_openai_auth = false\nsupports_websockets = false\n')
    directory.mkdir(parents=True, exist_ok=True)
    # Exclusive creation protects pre-existing generated files even if another installer raced us.
    mode = "w" if overwrite else "x"
    with catalog_path.open(mode) as file:
        file.write(catalog)
    with profile.open(mode) as file:
        file.write(content)
    return profile, catalog_path
