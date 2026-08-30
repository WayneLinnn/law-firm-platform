"""注册表：发现、依赖校验、拓扑序、装卸。"""
import pytest

from kernel.config import settings
from kernel.errors import ModuleCycleError, ModuleDependencyError, ModuleNotEnabled
from kernel.manifest import ModuleManifest
from kernel.registry import Registry, registry


@pytest.fixture(autouse=True)
def _restore_settings():
    original = settings.disabled_modules
    yield
    settings.disabled_modules = original
    registry.load(force=True)


def _registry_with(*manifests):
    reg = Registry()
    reg._manifests = {m.name: m for m in manifests}
    reg._order = reg._resolve()
    reg._loaded = True
    return reg


def test_discovers_modules():
    registry.load(force=True)
    assert "hello" in registry.order
    assert "identity" in registry.order
    assert registry.manifests["hello"].schema == "hello"
    assert registry.manifests["hello"].prefix == "/api/hello"


def test_module_can_be_disabled_by_config():
    settings.disabled_modules = "hello"
    registry.load(force=True)
    assert "hello" not in registry.order
    assert "hello" in registry.skipped
    with pytest.raises(ModuleNotEnabled):
        registry.api("hello")
    assert registry.optional_api("hello") is None


def test_api_is_the_public_entrypoint():
    registry.load(force=True)
    api = registry.api("hello")
    assert hasattr(api, "create_note")
    assert hasattr(api, "list_notes")


def test_missing_required_dependency_fails_fast():
    with pytest.raises(ModuleDependencyError):
        _registry_with(ModuleManifest(name="a", requires=("missing",)))


def test_missing_optional_dependency_degrades():
    reg = _registry_with(ModuleManifest(name="a", optional=("missing",)))
    assert reg.order == ["a"]
    assert reg.degraded["a"] == ("missing",)


def test_topological_order():
    reg = _registry_with(
        ModuleManifest(name="case", requires=("crm",)),
        ModuleManifest(name="crm"),
        ModuleManifest(name="conflict", requires=("crm",)),
    )
    order = reg.order
    assert order.index("crm") < order.index("case")
    assert order.index("crm") < order.index("conflict")


def test_cycle_is_rejected():
    with pytest.raises(ModuleCycleError):
        _registry_with(
            ModuleManifest(name="a", requires=("b",)),
            ModuleManifest(name="b", requires=("a",)),
        )


def test_schemas_and_permissions():
    registry.load(force=True)
    assert registry.schemas()[0] == "kernel"
    assert "hello" in registry.schemas()
    assert "hello.view" in registry.permissions()


def test_each_module_owns_its_migrations():
    registry.load(force=True)
    locations = registry.migration_locations()
    assert any("kernel" in loc for loc in locations)
    assert any("hello" in loc for loc in locations)


# ---------- 能力目录（将来接 Agent 用，现在只声明不消费） ----------

def test_capabilities_collected():
    registry.load(force=True)
    caps = registry.capabilities()
    assert any(c.name == "hello.listNotes" for c in caps)


def test_capability_exports_mcp_tool_schema():
    """Pydantic 原生出 JSON Schema —— 将来导出 MCP tools 零翻译成本。"""
    registry.load(force=True)
    tools = registry.mcp_tools()
    tool = next(t for t in tools if t["name"] == "hello.listNotes")
    assert tool["description"]
    schema = tool["inputSchema"]
    assert schema["type"] == "object"
    assert schema["properties"]["limit"]["type"] == "integer"
    assert schema["properties"]["limit"]["maximum"] == 100


def test_capability_defaults_to_read_only():
    """写操作必须显式声明，避免 Agent 误改数据。"""
    registry.load(force=True)
    for cap in registry.capabilities():
        assert cap.read_only is True, "{0} 是写操作，需显式确认并加审计".format(cap.name)


def test_capabilities_disappear_with_module():
    """拆掉模块，它的能力也一并消失 —— Agent 自动少会一件事。"""
    registry.load(force=True)
    assert any(c.name.startswith("hello.") for c in registry.capabilities())

    settings.disabled_modules = "hello"
    registry.load(force=True)
    assert not any(c.name.startswith("hello.") for c in registry.capabilities())


def test_broken_router_fails_loudly_not_silently():
    """★ router.py 里写错 import，路由不能**静默消失**。

    早先 submodule() 用 `except ImportError` 一把抓，结果模块的接口全没了，
    应用却照常启动 —— 这类 bug 极难排查。现在要抛错。
    """
    from pathlib import Path

    from kernel.errors import ModuleError

    registry.load(force=True)
    bad = Path(__file__).resolve().parent.parent / "modules" / "hello" / "_probe.py"
    bad.write_text("import a_module_that_does_not_exist\n", encoding="utf-8")
    try:
        with pytest.raises(ModuleError, match="依赖缺失"):
            registry.submodule("hello", "_probe")
    finally:
        bad.unlink()


def test_missing_optional_file_still_returns_none():
    """模块确实没有该文件时仍然返回 None（不是所有模块都有 router）。"""
    registry.load(force=True)
    assert registry.submodule("hello", "no_such_file") is None


def test_reverse_optional_dependency_is_not_a_cycle():
    """★ "A 可选依赖 B，B 必需依赖 A" 是合法的，不能判成环。

    这正是下游模块反过来卡住上游的标准做法：
    case 可选依赖 conflict —— 装了 conflict 就强制过利冲，没装就放行。
    可选依赖是运行时取的，与装载顺序无关。
    """
    reg = _registry_with(
        ModuleManifest(name="case", optional=("conflict",)),
        ModuleManifest(name="conflict", requires=("case",)),
    )
    assert reg.order.index("case") < reg.order.index("conflict")


def test_real_cycle_still_detected():
    """必需依赖成环仍然要报错。"""
    with pytest.raises(ModuleCycleError):
        _registry_with(
            ModuleManifest(name="a", requires=("b",)),
            ModuleManifest(name="b", requires=("a",)),
        )


def test_purge_reads_real_revision_ids(tmp_path):
    """★ purge 必须从迁移文件里**读出** revision，不能从文件名猜。

    回归：原实现是 `stem.split("_")[0] + "_" + stem.split("_")[1]`，
    对手写的 `hello_0001_init.py` 恰好蒙对，但 alembic 自动生成的
    `6d1af4734562_workflow_runs_and_steps.py` 会被猜成
    `6d1af4734562_workflow` —— 一条记录都删不掉。

    后果很隐蔽且致命：purge 把 schema DROP 掉了，版本记录却还在，
    `db upgrade` 于是认为"早升过了"直接跳过。**模块拆得掉、装不回来** ——
    "每个结构互相独立、可拆卸"这条铁律实际是破的。
    """
    from kernel.db.migrate import _revisions_in

    versions = tmp_path / "versions"
    versions.mkdir()
    (versions / "6d1af4734562_workflow_runs_and_steps.py").write_text(
        "revision = '6d1af4734562'\ndown_revision = None\n", encoding="utf-8")
    (versions / "hello_0001_init.py").write_text(
        'revision = "hello_0001"\ndown_revision = None\n', encoding="utf-8")
    (versions / "abc123_typed.py").write_text(
        'revision: str = "abc123"\ndown_revision: str | None = None\n', encoding="utf-8")
    (versions / "__init__.py").write_text("", encoding="utf-8")

    assert set(_revisions_in(versions)) == {"6d1af4734562", "hello_0001", "abc123"}
