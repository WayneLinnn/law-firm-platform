"""应用装配：无数据库也能启动；路由按模块自动挂载。"""
from fastapi.testclient import TestClient

from kernel.config import settings
from kernel.registry import registry
from kernel.web.app import create_app


def test_health_reports_modules():
    client = TestClient(create_app())
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert "hello" in body["modules"]["loaded"]


def test_module_route_mounted():
    client = TestClient(create_app())
    resp = client.get("/api/hello/ping")
    assert resp.status_code == 200
    assert resp.json() == {"module": "hello", "pong": True}


def test_app_starts_without_the_module():
    """拆卸测试：禁用 hello 后应用仍能启动，其路由消失。"""
    original = settings.disabled_modules
    settings.disabled_modules = "hello"
    try:
        registry.load(force=True)
        client = TestClient(create_app())
        assert client.get("/health").status_code == 200
        assert client.get("/api/hello/ping").status_code == 404
    finally:
        settings.disabled_modules = original
        registry.load(force=True)
