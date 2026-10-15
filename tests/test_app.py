import httpx
import pytest
from fastapi.testclient import TestClient

from app.main import app


@pytest.fixture
def client(monkeypatch):
    monkeypatch.delenv("HUMANIZER_API_KEY", raising=False)
    monkeypatch.delenv("HUMANIZER_MODEL", raising=False)
    monkeypatch.delenv("HUMANIZER_API_BASE", raising=False)
    with TestClient(app) as instance:
        yield instance


def post(client, **overrides):
    body = {"text": "值得注意的是，青禾团队调整了 12 个页面。此外，将继续改进搜索。", "tone": "natural", "engine": "demo"}
    body.update(overrides)
    return client.post("/api/rewrite", json=body, headers={"X-Humanizer-Request": "1"})


def configured(monkeypatch):
    monkeypatch.setenv("HUMANIZER_API_KEY", "fake-test-secret-never-real")
    monkeypatch.setenv("HUMANIZER_MODEL", "example-test-model")
    monkeypatch.setenv("HUMANIZER_API_BASE", "https://model.example.test/v1")


def mock_provider(monkeypatch, handler):
    original = httpx.AsyncClient
    monkeypatch.setattr(httpx, "AsyncClient", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_page_and_health(client):
    response = client.get("/")
    assert response.status_code == 200
    assert "说人话" in response.text
    assert "frame-ancestors 'none'" in response.headers["Content-Security-Policy"]
    assert client.get("/static/app.js").status_code == 200
    assert client.get("/api/health").json() == {"status": "ok"}


def test_demo_preserves_names_numbers_and_has_explanations(client):
    response = post(client)
    assert response.status_code == 200
    data = response.json()
    assert data["engine"] == "demo"
    assert "青禾团队" in data["text"] and "12" in data["text"]
    assert "值得注意的是" not in data["text"]
    assert "另外，" in data["text"]
    assert len(data["changes"]) == 2


def test_tone_choices_have_different_rule_behavior(client):
    natural = post(client, text="此外，这具有一定的帮助。", tone="natural").json()
    concise = post(client, text="此外，这具有一定的帮助。", tone="concise").json()
    professional = post(client, text="此外，这具有一定的帮助。", tone="professional").json()
    assert natural["text"] == "另外，这具有一定的帮助。"
    assert concise["text"] == "这有帮助。"
    assert professional["text"] == "此外，这具有一定的帮助。"


def test_unmatched_text_is_preserved(client):
    text = "这周完成了 3 项任务。\n下周测试新功能。"
    data = post(client, text=text).json()
    assert data["text"] == text
    assert data["changes"] == []


def test_only_filler_does_not_become_empty_result(client):
    data = post(client, text="综上所述。").json()
    assert data["text"] == "综上所述。"
    assert data["changes"] == []


@pytest.mark.parametrize("overrides", [{"text": "   "}, {"text": "文" * 6001}, {"tone": "invent"}, {"engine": "invent"}])
def test_rejects_invalid_input(client, overrides):
    assert post(client, **overrides).status_code == 422


def test_bounds_json_size_and_requires_custom_request_header(client):
    assert client.post("/api/rewrite", json={"text": "hello"}).status_code == 403
    assert client.post("/api/rewrite", content="{}", headers={"X-Humanizer-Request": "1"}).status_code == 415
    response = client.post("/api/rewrite", content=b"x" * 65537, headers={"Content-Type": "application/json", "X-Humanizer-Request": "1"})
    assert response.status_code == 413


def test_configuration_never_exposes_key(client, monkeypatch):
    assert client.get("/api/config").json()["model_available"] is False
    configured(monkeypatch)
    response = client.get("/api/config")
    assert response.json()["provider_host"] == "model.example.test"
    assert response.json()["model_available"] is True
    assert "fake-test-secret" not in response.text
    assert "example-test-model" not in response.text


def test_model_requires_explicit_consent_and_configuration(client):
    assert post(client, engine="model").status_code == 400
    assert post(client, engine="model", consent=True).status_code == 503


def test_model_aggregates_text_after_reasoning_and_sends_no_store(client, monkeypatch):
    configured(monkeypatch)

    def handler(request):
        import json
        body = json.loads(request.content)
        assert request.url == "https://model.example.test/v1/responses"
        assert request.headers["Authorization"] == "Bearer fake-test-secret-never-real"
        assert body["store"] is False
        assert body["model"] == "example-test-model"
        assert "原文没有" in body["instructions"]
        return httpx.Response(200, json={"status": "completed", "output": [{"type": "reasoning"}, {"type": "message", "role": "assistant", "content": [{"type": "output_text", "text": "第一段。"}, {"type": "output_text", "text": "第二段。"}]}]})

    mock_provider(monkeypatch, handler)
    response = post(client, engine="model", consent=True)
    assert response.status_code == 200
    assert response.json()["text"] == "第一段。\n第二段。"


@pytest.mark.parametrize("payload", [{"status": "incomplete", "output": []}, {"status": "completed", "output": []}, {"status": "completed", "output": [{"type": "message", "role": "assistant", "content": "invalid"}]}, []])
def test_invalid_model_output_is_not_presented_as_success(client, monkeypatch, payload):
    configured(monkeypatch)
    mock_provider(monkeypatch, lambda request: httpx.Response(200, json=payload))
    assert post(client, engine="model", consent=True).status_code == 502


def test_provider_error_does_not_leak_upstream_body(client, monkeypatch):
    configured(monkeypatch)
    mock_provider(monkeypatch, lambda request: httpx.Response(401, text="fake-test-secret-never-real PRIVATE ORIGINAL TEXT"))
    response = post(client, engine="model", consent=True)
    assert response.status_code == 502
    assert "PRIVATE" not in response.text and "fake-test-secret" not in response.text


def test_provider_timeout_is_explained(client, monkeypatch):
    configured(monkeypatch)

    def handler(request):
        raise httpx.ReadTimeout("test timeout", request=request)

    mock_provider(monkeypatch, handler)
    assert post(client, engine="model", consent=True).status_code == 504


def test_rejects_insecure_provider_url(client, monkeypatch):
    configured(monkeypatch)
    monkeypatch.setenv("HUMANIZER_API_BASE", "http://model.example.test/v1")
    assert post(client, engine="model", consent=True).status_code == 503
