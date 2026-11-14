import yaml
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from api import create_app
from core.config import AppConfig


@pytest.fixture
def upstream(monkeypatch):
    state = {
        "status": 200,
        "payload": {
            "object": "kemo.model_list",
            "data": [
                {"id": "chat-a", "task": "llm", "provider_id": "gateway", "internal": "not-public"},
                {"id": "embed-a", "task": "embedding", "provider_id": "vectors"},
                {"id": "rerank-a", "task": "rerank", "provider_id": "vectors"},
                {"id": "unknown-a", "task": "unknown", "provider_id": "gateway"},
            ],
        },
        "failure": None,
        "requests": [],
    }

    def handle(request):
        state["requests"].append(request)
        if state["failure"] == "timeout":
            raise httpx.ReadTimeout("upstream-secret", request=request)
        if state["failure"] == "network":
            raise httpx.ConnectError("upstream-secret", request=request)
        if state["failure"] == "not-json":
            return httpx.Response(200, text="upstream-secret")
        return httpx.Response(state["status"], json=state["payload"])

    client_type = httpx.Client
    monkeypatch.setattr(
        "provider.httpx.Client",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )
    return state


def app_for(tmp_path: Path, **kemo):
    config = AppConfig(
        log_dir=str(tmp_path / "log"),
        kemo={"base_url": "https://gateway.test", "api_key": "catalog-test-secret", **kemo},
    )
    config_path = tmp_path / "config.yaml"
    config_path.write_text(yaml.safe_dump(config.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
    return create_app(
        config_path=config_path,
        data_dir=tmp_path / "data",
        external_dir=tmp_path / "markdown",
    )


def test_catalog_uses_saved_credentials_and_sanitizes_public_metadata(tmp_path, upstream):
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/v1/config/models")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json()["data"]["models"] == [
        {"id": model["id"], "task": model["task"], "provider_id": model["provider_id"]}
        for model in upstream["payload"]["data"]
    ]
    assert "catalog-test-secret" not in response.text
    assert "not-public" not in response.text
    request = upstream["requests"][0]
    assert str(request.url) == "https://gateway.test/model/models"
    assert request.headers["authorization"] == "Bearer catalog-test-secret"
    assert request.headers["x-kemo-protocol-version"] == "2.0"
    assert request.method == "GET" and not request.content
    assert request.extensions["timeout"]["read"] == 20


def test_catalog_uses_environment_key_and_updated_saved_config(tmp_path, upstream, monkeypatch):
    monkeypatch.setenv("KEMO_CATALOG_TEST_KEY", "environment-test-secret")
    with TestClient(app_for(tmp_path, api_key="", api_key_env="KEMO_CATALOG_TEST_KEY")) as client:
        assert client.get("/api/v1/config/models").status_code == 200
        config = client.get("/api/v1/config").json()["data"]
        config["kemo"]["base_url"] = "https://new-gateway.test/"
        config["kemo"]["api_key"] = "new-config-test-secret"
        saved = client.put("/api/v1/config", json=config)
        assert saved.status_code == 200
        assert "new-config-test-secret" not in saved.text
        assert client.get("/api/v1/config/models").status_code == 200
        # 回写掩码后仍使用服务端真实密钥。
        assert client.put("/api/v1/config", json=saved.json()["data"]).status_code == 200
        assert client.get("/api/v1/config/models").status_code == 200

    assert upstream["requests"][0].headers["authorization"] == "Bearer environment-test-secret"
    for request in upstream["requests"][1:]:
        assert str(request.url) == "https://new-gateway.test/model/models"
        assert request.headers["authorization"] == "Bearer new-config-test-secret"


def test_missing_key_does_not_contact_upstream(tmp_path, upstream, monkeypatch):
    monkeypatch.delenv("KEMO_CATALOG_MISSING_KEY", raising=False)
    with TestClient(app_for(tmp_path, api_key="", api_key_env="KEMO_CATALOG_MISSING_KEY")) as client:
        response = client.get("/api/v1/config/models")
    assert response.status_code == 400
    assert response.json()["error"]["code"] == "MODEL_CATALOG_NOT_CONFIGURED"
    assert not upstream["requests"]


def test_empty_catalog_is_not_replaced_by_configured_or_invented_models(tmp_path, upstream):
    upstream["payload"] = {"data": []}
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/v1/config/models")
    assert response.status_code == 200
    assert response.json()["data"] == {"models": []}


@pytest.mark.parametrize("status", [401, 403])
def test_invalid_or_forbidden_key_has_clear_error_without_upstream_body(tmp_path, upstream, status):
    upstream["status"] = status
    upstream["payload"] = {"error": {"message": "upstream-secret"}}
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/v1/config/models")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_CATALOG_AUTH_FAILED"
    assert "upstream-secret" not in response.text


@pytest.mark.parametrize(
    ("failure", "status", "code"),
    [
        ("timeout", 504, "MODEL_CATALOG_TIMEOUT"),
        ("network", 502, "MODEL_CATALOG_UNAVAILABLE"),
        ("not-json", 502, "MODEL_CATALOG_UNAVAILABLE"),
    ],
)
def test_transport_failures_are_explicit_and_do_not_expose_details(tmp_path, upstream, failure, status, code):
    upstream["failure"] = failure
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/v1/config/models")
    assert response.status_code == status
    assert response.json()["error"]["code"] == code
    assert "upstream-secret" not in response.text


@pytest.mark.parametrize(
    "payload",
    [
        {"data": None},
        {"data": [{"id": "", "task": "llm", "provider_id": "gateway"}]},
        {"data": [{"id": "upstream-secret", "task": "other", "provider_id": "gateway"}]},
    ],
)
def test_invalid_catalog_is_reported_instead_of_becoming_a_successful_empty_list(tmp_path, upstream, payload):
    upstream["payload"] = payload
    with TestClient(app_for(tmp_path)) as client:
        response = client.get("/api/v1/config/models")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "MODEL_CATALOG_UNAVAILABLE"
    assert "upstream-secret" not in response.text
