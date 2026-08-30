#!/usr/bin/env python
"""模块边界检查 —— CI 必跑。违反即 exit 1。

三条铁律：
  1. kernel/*   不得 import modules.* 或 adapters.*（bootstrap.py 例外）
  2. modules/X  不得 import adapters.*；跨模块只能 import modules.Y.api
  3. adapters/* 不得 import modules.*
另外校验每个模块必须有 manifest.py 与 api.py。
"""
from __future__ import annotations

import ast
import sys
from pathlib import Path
from typing import List, Optional, Tuple

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover
            pass

BACKEND = Path(__file__).resolve().parent.parent
KERNEL_IMPORT_EXCEPTIONS = {"kernel/bootstrap.py"}
REQUIRED_MODULE_FILES = ("manifest.py", "api.py")

Violation = Tuple[str, int, str]


def rel(path: Path) -> str:
    return path.relative_to(BACKEND).as_posix()


def resolve(node: ast.ImportFrom, file: Path) -> Optional[str]:
    """把相对 import 还原成绝对模块路径。"""
    if node.level == 0:
        return node.module
    parts = rel(file).replace(".py", "").split("/")
    if file.name == "__init__.py":
        parts = parts[:-1]
    base = parts[: len(parts) - node.level]
    if node.module:
        base = base + node.module.split(".")
    return ".".join(base)


def imported_targets(tree: ast.AST, file: Path) -> List[Tuple[str, int]]:
    """列出该文件实际触达的模块路径。"""
    out: List[Tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                out.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            base = resolve(node, file)
            if not base:
                continue
            for alias in node.names:
                out.append(("{0}.{1}".format(base, alias.name), node.lineno))
            out.append((base, node.lineno))
    return out


def check_file(file: Path) -> List[Violation]:
    violations: List[Violation] = []
    relative = rel(file)
    try:
        tree = ast.parse(file.read_text(encoding="utf-8"))
    except SyntaxError as exc:
        return [(relative, exc.lineno or 0, "语法错误: {0}".format(exc.msg))]

    in_kernel = relative.startswith("kernel/")
    in_adapters = relative.startswith("adapters/")
    in_modules = relative.startswith("modules/")
    own_module = relative.split("/")[1] if in_modules and len(relative.split("/")) > 2 else None

    for target, line in imported_targets(tree, file):
        parts = target.split(".")
        root = parts[0]

        if in_kernel and relative not in KERNEL_IMPORT_EXCEPTIONS:
            if root in ("modules", "adapters"):
                violations.append(
                    (relative, line, "内核不得依赖 {0}（应通过 ports/registry 反转依赖）".format(target))
                )

        if in_adapters and root == "modules":
            violations.append((relative, line, "adapters 不得依赖业务模块 {0}".format(target)))

        if in_modules:
            if root == "adapters":
                violations.append(
                    (relative, line, "业务模块不得直接依赖 {0}，请改用 kernel.container".format(target))
                )
            elif root == "modules" and len(parts) >= 2 and parts[1] != own_module:
                if len(parts) < 3 or parts[2] != "api":
                    violations.append(
                        (relative, line,
                         "跨模块只能访问 modules.{0}.api，实际 import 了 {1}".format(parts[1], target))
                    )
    return violations


def check_structure() -> List[Violation]:
    violations: List[Violation] = []
    modules_dir = BACKEND / "modules"
    if not modules_dir.exists():
        return violations
    for child in sorted(modules_dir.iterdir()):
        if not child.is_dir() or child.name.startswith("_"):
            continue
        for required in REQUIRED_MODULE_FILES:
            if not (child / required).exists():
                violations.append(
                    ("modules/{0}".format(child.name), 0, "缺少约定文件 {0}".format(required))
                )
    return violations


def main() -> int:
    violations: List[Violation] = list(check_structure())
    for root in ("kernel", "modules", "adapters"):
        base = BACKEND / root
        if not base.exists():
            continue
        for file in sorted(base.rglob("*.py")):
            if "__pycache__" in file.parts or "migrations" in file.parts:
                continue
            violations.extend(check_file(file))

    if violations:
        print("边界检查未通过：")
        for path, line, message in violations:
            print("  {0}:{1}  {2}".format(path, line, message))
        return 1
    print("边界检查通过：模块之间没有非法依赖。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
