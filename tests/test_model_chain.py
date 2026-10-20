from __future__ import annotations

from dra.config import Settings
from dra.llm import ModelUnavailable, is_model_level_error


def test_model_chain_starts_with_primary_and_dedupes():
    settings = Settings(
        qwen_chat_model_1="m-a",
        qwen_chat_model_2="m-b",
        qwen_chat_model_3="m-a",
        qwen_chat_model_4="",
    )
    chain = settings.model_chain("m-b")
    assert chain[0] == "m-b"
    assert chain.count("m-a") == 1
    assert "" not in chain
    assert len(chain) == len(set(chain))


def test_all_models_are_ordered():
    settings = Settings(qwen_chat_model_1="x", qwen_chat_model_2="y")
    assert settings.all_models[:2] == ["x", "y"]


def test_model_level_error_detection():
    assert is_model_level_error(ModelUnavailable("quota exceeded"))
    assert is_model_level_error(RuntimeError("model not found: foo"))
    assert not is_model_level_error(RuntimeError("connection reset"))
