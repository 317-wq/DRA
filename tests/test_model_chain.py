from __future__ import annotations

from dra.config import Settings
from dra.llm import ModelUnavailable, is_model_level_error


def test_model_chain_starts_with_primary_and_dedupes():
    settings = Settings(
        model="m-a",
        fallback_models=["m-b", "m-a", ""],
        _env_file=None,
    )
    chain = settings.model_chain("m-b")
    assert chain[0] == "m-b"
    assert chain.count("m-a") == 1
    assert "" not in chain
    assert len(chain) == len(set(chain))


def test_all_models_keep_order():
    settings = Settings(model="x", fallback_models=["y", "z"], _env_file=None)
    assert settings.all_models == ["x", "y", "z"]


def test_role_models_fall_back_to_default():
    settings = Settings(model="base", plan_model="", _env_file=None)
    assert settings.plan_model_resolved == "base"
    assert settings.analyze_model_resolved == "base"
    assert settings.write_model_resolved == "base"
    assert Settings(model="base", write_model="w", _env_file=None).write_model_resolved == "w"


def test_model_level_error_detection():
    assert is_model_level_error(ModelUnavailable("quota exceeded"))
    assert is_model_level_error(RuntimeError("model not found: foo"))
    assert not is_model_level_error(RuntimeError("connection reset"))
