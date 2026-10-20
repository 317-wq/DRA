from __future__ import annotations

import sys

from dra.config import load_toml
from dra.onboarding import PRESETS, save_config


def test_presets_have_required_fields():
    for item in PRESETS.values():
        assert {"label", "base_url", "model"} <= set(item)


def test_save_config_writes_expected_keys(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("DRA_CONFIG_FILE", str(cfg))
    path = save_config(
        base_url="https://example.com/v1",
        api_key="k-123",
        model="m-1",
        tavily_api_key="t-456",
        embedding_model="emb",
        runs_dir=str(tmp_path / "data"),
    )
    data = load_toml(path)
    assert data["base_url"] == "https://example.com/v1"
    assert data["api_key"] == "k-123"
    assert data["model"] == "m-1"
    assert data["tavily_api_key"] == "t-456"
    assert data["embedding_model"] == "emb"
    assert data["runs_dir"] == str(tmp_path / "data")
    if sys.platform != "win32":
        assert oct(path.stat().st_mode)[-3:] == "600"


def test_save_config_skips_empty_optional(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("DRA_CONFIG_FILE", str(cfg))
    save_config(
        base_url="https://example.com/v1",
        api_key="k",
        model="m",
        tavily_api_key="",
    )
    data = load_toml(cfg)
    assert "tavily_api_key" not in data
