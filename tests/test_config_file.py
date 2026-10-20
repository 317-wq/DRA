from __future__ import annotations

import sys

from dra.config import Settings, config_path, load_toml, mask_secret, write_toml_config


def test_toml_roundtrip(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("DRA_CONFIG_FILE", str(cfg))
    write_toml_config(
        {
            "base_url": "https://example.com/v1",
            "api_key": "secret-key-123",
            "model": "file-model",
            "runs_dir": str(tmp_path / "runs"),
        },
        cfg,
    )
    assert config_path() == cfg
    data = load_toml()
    assert data["api_key"] == "secret-key-123"

    settings = Settings(_env_file=None)
    assert settings.api_key == "secret-key-123"
    assert settings.model == "file-model"


def test_env_overrides_toml(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("DRA_CONFIG_FILE", str(cfg))
    write_toml_config({"api_key": "from-file", "model": "file-model"}, cfg)
    monkeypatch.setenv("DRA_API_KEY", "from-env")
    settings = Settings(_env_file=None)
    assert settings.api_key == "from-env"
    assert settings.model == "file-model"


def test_kwargs_override_env(tmp_path, monkeypatch):
    monkeypatch.setenv("DRA_CONFIG_FILE", str(tmp_path / "none.toml"))
    monkeypatch.setenv("DRA_API_KEY", "from-env")
    assert Settings(api_key="from-arg", _env_file=None).api_key == "from-arg"


def test_config_file_permissions(tmp_path, monkeypatch):
    cfg = tmp_path / "config.toml"
    monkeypatch.setenv("DRA_CONFIG_FILE", str(cfg))
    write_toml_config({"api_key": "k"}, cfg)
    if sys.platform != "win32":
        assert oct(cfg.stat().st_mode)[-3:] == "600"


def test_public_summary_masks_secrets():
    settings = Settings(api_key="abcdefgh12345678", _env_file=None)
    summary = settings.public_summary()
    assert "abcdefgh12345678" not in summary["api_key"]
    assert summary["api_key"].startswith("abcd")


def test_mask_secret_edge_cases():
    assert mask_secret("") == "(未配置)"
    assert mask_secret("short") == "****"
