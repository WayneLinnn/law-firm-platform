"""模块注册表：发现、校验依赖、拓扑排序、按需取用。

装卸模块的两种方式，都不需要改代码：
  1. 删掉 modules/<name>/ 目录
  2. 设置 LFP_DISABLED_MODULES=<name>
"""
from __future__ import annotations

import importlib
import logging
import pkgutil
from types import ModuleType
from typing import Any, Dict, List, Optional, Tuple

from kernel.config import settings
from kernel.errors import (
    ModuleCycleError,
    ModuleDependencyError,
    ModuleError,
    ModuleNotEnabled,
)
from kernel.manifest import ModuleManifest

logger = logging.getLogger(__name__)

MODULES_PACKAGE = "modules"


class Registry:
    """全局唯一实例见文件末尾的 `registry`。"""

    def __init__(self) -> None:
        self._manifests: Dict[str, ModuleManifest] = {}
        self._order: List[str] = []
        self._degraded: Dict[str, Tuple[str, ...]] = {}
        self._skipped: List[str] = []
        self._loaded = False

    # ---------- 装载 ----------

    def load(self, force: bool = False) -> "Registry":
        if self._loaded and not force:
            return self
        self._reset()
        discovered = self._discover()
        for name, manifest in discovered.items():
            if settings.is_module_allowed(name):
                self._manifests[name] = manifest
            else:
                self._skipped.append(name)
        self._order = self._resolve()
        self._loaded = True
        logger.info(
            "已装载模块 %s；跳过 %s；降级 %s",
            self._order, self._skipped, self._degraded,
        )
        return self

    def _reset(self) -> None:
        self._manifests = {}
        self._order = []
        self._degraded = {}
        self._skipped = []
        self._loaded = False

    def _discover(self) -> Dict[str, ModuleManifest]:
        """扫描 modules/ 下的每个包，读取它的 MANIFEST。"""
        found: Dict[str, ModuleManifest] = {}
        try:
            pkg = importlib.import_module(MODULES_PACKAGE)
        except ImportError as exc:  # pragma: no cover - 目录必然存在
            raise ModuleError("找不到 modules 包: {0}".format(exc)) from exc

        for info in pkgutil.iter_modules(pkg.__path__):
            if not info.ispkg or info.name.startswith("_"):
                continue
            dotted = "{0}.{1}.manifest".format(MODULES_PACKAGE, info.name)
            try:
                mod = importlib.import_module(dotted)
            except ImportError as exc:
                raise ModuleError(
                    "模块 {0} 缺少 manifest.py（约定：每个模块必须有）: {1}".format(info.name, exc)
                ) from exc
            manifest = getattr(mod, "MANIFEST", None)
            if not isinstance(manifest, ModuleManifest):
                raise ModuleError(
                    "模块 {0} 的 manifest.py 必须定义 MANIFEST = ModuleManifest(...)".format(info.name)
                )
            if manifest.name != info.name:
                raise ModuleError(
                    "模块目录名 {0} 与 MANIFEST.name {1} 不一致".format(info.name, manifest.name)
                )
            found[info.name] = manifest
        return found

    def _resolve(self) -> List[str]:
        """拓扑排序 + 依赖校验。必需依赖缺失 -> 直接报错；可选依赖缺失 -> 记为降级。"""
        for name, manifest in self._manifests.items():
            missing_required = [d for d in manifest.requires if d not in self._manifests]
            if missing_required:
                raise ModuleDependencyError(
                    "模块 {0} 的必需依赖缺失: {1}".format(name, ", ".join(missing_required))
                )
            missing_optional = tuple(d for d in manifest.optional if d not in self._manifests)
            if missing_optional:
                self._degraded[name] = missing_optional

        visiting: set = set()
        done: set = set()
        order: List[str] = []

        def visit(node: str, path: List[str]) -> None:
            if node in done:
                return
            if node in visiting:
                raise ModuleCycleError("模块依赖成环: {0}".format(" -> ".join(path + [node])))
            visiting.add(node)
            manifest = self._manifests[node]
            # ★ 只有必需依赖参与排序。可选依赖是**运行时**通过
            #   registry.optional_api() 取的，装载顺序无关紧要。
            #   若把可选依赖也算进边，"A 可选依赖 B、B 必需依赖 A" 这种
            #   完全合法的反向可选依赖会被误判成环 ——
            #   而它恰恰是让下游模块能反过来卡住上游的标准做法
            #   （case 可选依赖 conflict：装了就强制过利冲，没装就放行）。
            deps = list(manifest.requires)
            for dep in sorted(deps):
                visit(dep, path + [node])
            visiting.discard(node)
            done.add(node)
            order.append(node)

        for name in sorted(self._manifests):
            visit(name, [])
        return order

    # ---------- 取用 ----------

    def has(self, name: str) -> bool:
        self.load()
        return name in self._manifests

    def api(self, name: str) -> ModuleType:
        """取另一个模块的公开出口。这是模块间唯一合法的调用方式。"""
        self.load()
        if name not in self._manifests:
            raise ModuleNotEnabled("模块 {0} 未启用或不存在".format(name))
        return importlib.import_module("{0}.{1}.api".format(MODULES_PACKAGE, name))

    def optional_api(self, name: str) -> Optional[ModuleType]:
        """可选依赖的取法：拿不到就返回 None，调用方自行降级。"""
        self.load()
        if name not in self._manifests:
            return None
        return importlib.import_module("{0}.{1}.api".format(MODULES_PACKAGE, name))

    def submodule(self, name: str, part: str) -> Optional[ModuleType]:
        """取模块内的某个文件（router/models），**文件不存在**返回 None。

        ★ 只吞"文件不存在"，不吞"文件里有错"。
          早先这里 except ImportError 一把抓，结果 router.py 里写错一个
          import，整个模块的路由会**静默消失** —— 应用照常启动，接口全没了，
          排查起来极痛苦。
        """
        self.load()
        if name not in self._manifests:
            return None
        dotted = "{0}.{1}.{2}".format(MODULES_PACKAGE, name, part)
        try:
            return importlib.import_module(dotted)
        except ModuleNotFoundError as exc:
            if getattr(exc, "name", None) == dotted:
                return None          # 该模块确实没有这个文件，正常
            raise ModuleError(
                "模块 {0} 的 {1} 依赖缺失：{2}".format(name, part, exc)
            ) from exc

    # ---------- 元信息 ----------

    @property
    def order(self) -> List[str]:
        self.load()
        return list(self._order)

    @property
    def manifests(self) -> Dict[str, ModuleManifest]:
        self.load()
        return dict(self._manifests)

    @property
    def degraded(self) -> Dict[str, Tuple[str, ...]]:
        self.load()
        return dict(self._degraded)

    @property
    def skipped(self) -> List[str]:
        self.load()
        return list(self._skipped)

    def schemas(self) -> List[str]:
        """需要 CREATE SCHEMA 的名字：内核 + 每个启用模块。"""
        return ["kernel"] + self.order

    def permissions(self) -> List[str]:
        """所有模块注册的权限点（Step 4 由 identity 消费）。"""
        result: List[str] = []
        for name in self.order:
            result.extend(self._manifests[name].permissions)
        return result

    def capabilities(self) -> List[Any]:
        """所有模块暴露给 Agent 的动作。

        【现在只汇总，不消费】将来接 Agent 时，这就是 MCP tool 清单的来源。
        加模块 = Agent 自动多会几件事，Agent 侧代码一行不改。见手册第 10 章。
        """
        result: List[Any] = []
        for name in self.order:
            result.extend(self._manifests[name].capabilities)
        return result

    def inbox_modules(self) -> List[str]:
        """哪些模块有「等人动手的事」要报进统一待办中心。

        和 metrics 一个模式：**各模块自报，待办中心只汇总**。
        加一个模块 = 待办中心自动多一类事，它一行不改。

        ★ 这里只返回模块名，不返回条目 —— 待办是**跟人走**的，
          没法像 Metric 那样静态声明，得拿着 principal 去问 api.inbox_items()。
        """
        self.load()
        return [n for n in self.order
                if getattr(self._manifests[n], "has_inbox", False)]

    def metrics(self) -> List[Any]:
        """所有模块能往看板上报的指标（Step 21）。

        和 capabilities 一个模式：**各模块自报，看板只汇总**。
        加一个模块 = 看板自动多几个指标，analytics 一行不改。
        """
        result: List[Any] = []
        for name in self.order:
            result.extend(getattr(self._manifests[name], "metrics", ()) or ())
        return result

    def mcp_tools(self) -> List[dict]:
        """把能力目录导出成 MCP tools 结构。一步遍历，零翻译成本。"""
        return [cap.as_mcp_tool() for cap in self.capabilities()]

    def import_models(self) -> None:
        """把所有模块的 models 导入，供 Alembic 收集 metadata。"""
        for name in self.order:
            self.submodule(name, "models")

    def migration_locations(self) -> List[str]:
        """各模块自带的迁移目录。删模块 = 迁移脚本一起消失。"""
        import os

        locations = [os.path.join(os.path.dirname(__file__), "db", "migrations", "versions")]
        for name in self.order:
            pkg = importlib.import_module("{0}.{1}".format(MODULES_PACKAGE, name))
            path = os.path.join(os.path.dirname(pkg.__file__), "migrations", "versions")
            if os.path.isdir(path):
                locations.append(path)
        return locations

    def describe(self) -> dict:
        self.load()
        return {
            "loaded": self.order,
            "skipped": self.skipped,
            "degraded": {k: list(v) for k, v in self.degraded.items()},
            "modules": {
                name: {
                    "version": m.version,
                    "description": m.description,
                    "requires": list(m.requires),
                    "optional": list(m.optional),
                    "schema": m.schema,
                    "prefix": m.prefix,
                }
                for name, m in self.manifests.items()
            },
        }


registry = Registry()
