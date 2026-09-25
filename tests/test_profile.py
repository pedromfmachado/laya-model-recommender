import json
import tomllib

import pytest

from laya_router.config import load_settings
from laya_router.profile import install_profile


def descriptor(slug):
    return {"slug": slug, "display_name": slug, "input_modalities": ["text", "image"],
            "context_window": 272000, "max_context_window": 872000, "use_responses_lite": True,
            "tool_mode": "code_mode_only", "base_instructions": "full descriptor preserved",
            "experimental_supported_tools": ["unverified"], "supports_search_tool": True}


def test_install_preserves_complete_descriptor_and_base_config(tmp_path):
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"models": [descriptor(name) for name in ["gpt-5.6-luna", "gpt-5.6-terra", "gpt-5.6-sol"]]}))
    base = tmp_path / "config.toml"
    base.write_text('model="original"\n')
    profile, catalog = install_profile(load_settings("routes.example.toml"), tmp_path, catalog_source=source)
    parsed = tomllib.loads(profile.read_text())
    model = json.loads(catalog.read_text())["models"][0]
    assert model["slug"] == "laya-auto" and model["display_name"] == "Laya Auto"
    assert model["base_instructions"] == "full descriptor preserved"
    assert model["context_window"] == model["max_context_window"] == 272000
    assert model["use_responses_lite"] is False and model["experimental_supported_tools"] == []
    assert "tool_mode" not in model
    assert parsed["model_providers"]["laya"]["supports_websockets"] is False
    assert base.read_text() == 'model="original"\n'
    with pytest.raises(ValueError, match="overwrite"):
        install_profile(load_settings("routes.example.toml"), tmp_path, catalog_source=source)
    install_profile(load_settings("routes.example.toml"), tmp_path, catalog_source=source, overwrite=True)


def test_install_requires_capability_metadata(tmp_path):
    config = tmp_path / "routes.toml"
    config.write_text('[routes.general]\nmodel="unconfigured"\n')
    source = tmp_path / "source.json"
    source.write_text(json.dumps({"models": [descriptor("gpt-5.6-sol")]}))
    with pytest.raises(ValueError, match="canonical GPT"):
        install_profile(load_settings(config), tmp_path, catalog_source=source)
