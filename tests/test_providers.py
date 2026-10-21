from __future__ import annotations

import pytest

from dra import providers


class FakeResponse:
    def __init__(self, status_code: int = 200, data=None, text: str = "") -> None:
        self.status_code = status_code
        self._data = data
        self.text = text

    def json(self):
        if self._data is None:
            raise ValueError("not json")
        return self._data


def test_provider_presets():
    items = providers.provider_list()
    ids = {item["id"] for item in items}
    assert {"dashscope", "openai", "deepseek", "ollama", "custom"} <= ids
    assert all("base_url" in item for item in items)


def test_fetch_models(monkeypatch):
    monkeypatch.setattr(
        providers.httpx,
        "get",
        lambda url, headers=None, timeout=None: FakeResponse(
            200, {"data": [{"id": "b-model"}, {"id": "a-model"}, {"id": "a-model"}]}
        ),
    )
    models = providers.fetch_models("https://example.com/v1/", "key")
    assert models == ["a-model", "b-model"]


def test_fetch_models_http_error(monkeypatch):
    monkeypatch.setattr(
        providers.httpx,
        "get",
        lambda url, headers=None, timeout=None: FakeResponse(401, None, "unauthorized"),
    )
    with pytest.raises(providers.ProviderError, match="401"):
        providers.fetch_models("https://example.com/v1", "bad")


def test_test_chat(monkeypatch):
    monkeypatch.setattr(
        providers.httpx,
        "post",
        lambda url, headers=None, json=None, timeout=None: FakeResponse(
            200, {"model": "test-model", "choices": [{"message": {"content": "OK"}}]}
        ),
    )
    result = providers.test_chat("https://example.com/v1", "key", "test-model")
    assert result["ok"] is True
    assert result["model"] == "test-model"
    assert result["reply"] == "OK"
    assert result["latency_ms"] >= 0


def test_base_url_validation():
    with pytest.raises(providers.ProviderError):
        providers.fetch_models("", "key")
    with pytest.raises(providers.ProviderError):
        providers.fetch_models("ftp://example.com", "key")
