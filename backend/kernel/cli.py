"""内核命令行。

  python -m kernel.cli modules                    列出装载/跳过/降级的模块
  python -m kernel.cli schemas                    列出需要创建的 schema
  python -m kernel.cli permissions                列出所有权限点
  python -m kernel.cli capabilities [--mcp]      列出 Agent 能力目录
  python -m kernel.cli check                      边界检查（CI 必跑）
  python -m kernel.cli db upgrade                 所有模块迁移到最新
  python -m kernel.cli db revision hello -m "xx"  给某模块新增迁移
  python -m kernel.cli db current|heads|history
  python -m kernel.cli db purge hello             拆除模块的 schema 与迁移记录
                                                 ★ **必须在删模块目录之前跑**
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

from kernel.bootstrap import bootstrap
from kernel.registry import registry

# Windows 控制台默认 GBK，强制 UTF-8 以免中文输出乱码
for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        try:
            _stream.reconfigure(encoding="utf-8")
        except Exception:  # pragma: no cover
            pass


def cmd_modules(_args) -> int:
    bootstrap()
    info = registry.describe()
    print("已装载 ({0}): {1}".format(len(info["loaded"]), ", ".join(info["loaded"]) or "-"))
    print("已跳过: {0}".format(", ".join(info["skipped"]) or "-"))
    for name, missing in info["degraded"].items():
        print("降级运行: {0} 缺少可选依赖 {1}".format(name, ", ".join(missing)))
    print()
    for name, meta in info["modules"].items():
        print(
            "  {0:<12} v{1:<8} schema={2:<12} prefix={3:<16} requires={4} optional={5}".format(
                name, meta["version"], meta["schema"], meta["prefix"],
                meta["requires"] or "-", meta["optional"] or "-",
            )
        )
    return 0


def cmd_schemas(_args) -> int:
    bootstrap()
    print(json.dumps(registry.schemas(), ensure_ascii=False))
    return 0


def cmd_permissions(_args) -> int:
    bootstrap()
    perms = registry.permissions()
    if not perms:
        print("（暂无权限点，Step 4 identity 落地后由各模块注册）")
    for p in perms:
        print(p)
    return 0


def cmd_capabilities(args) -> int:
    """列出所有模块暴露给 Agent 的动作（将来的 MCP tool 清单）。"""
    bootstrap()
    caps = registry.capabilities()
    if not caps:
        print("（暂无模块声明 capabilities）")
        return 0
    if args.mcp:
        print(json.dumps(registry.mcp_tools(), ensure_ascii=False, indent=2))
        return 0
    for cap in caps:
        flag = "只读" if cap.read_only else "写★需二次确认"
        print("  {0:<24} [{1}] perm={2:<16} {3}".format(
            cap.name, flag, cap.requires_perm or "-", cap.description))
    print("\n共 {0} 个能力。加 --mcp 查看 MCP tools 结构。".format(len(caps)))
    return 0


def cmd_check(_args) -> int:
    script = Path(__file__).resolve().parent.parent / "scripts" / "check_boundaries.py"
    return subprocess.call([sys.executable, str(script)])


def cmd_db(args) -> int:
    from kernel.db import migrate

    bootstrap()
    if args.db_command == "upgrade":
        migrate.upgrade(args.target)
    elif args.db_command == "downgrade":
        migrate.downgrade(args.target)
    elif args.db_command == "current":
        migrate.current()
    elif args.db_command == "heads":
        migrate.heads()
    elif args.db_command == "history":
        migrate.history()
    elif args.db_command == "revision":
        migrate.new_revision(args.module, args.message, autogenerate=not args.empty)
    elif args.db_command == "purge":
        migrate.purge_module(args.module, revisions=args.revision or None)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="kernel.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("modules", help="列出模块")
    sub.add_parser("schemas", help="列出 schema")
    sub.add_parser("permissions", help="列出权限点")
    caps = sub.add_parser("capabilities", help="列出 Agent 能力目录")
    caps.add_argument("--mcp", action="store_true", help="输出 MCP tools 结构")
    sub.add_parser("check", help="边界检查")

    db = sub.add_parser("db", help="数据库迁移")
    dbsub = db.add_subparsers(dest="db_command", required=True)

    up = dbsub.add_parser("upgrade")
    up.add_argument("target", nargs="?", default="heads")
    down = dbsub.add_parser("downgrade")
    down.add_argument("target")
    dbsub.add_parser("current")
    dbsub.add_parser("heads")
    dbsub.add_parser("history")
    rev = dbsub.add_parser("revision")
    rev.add_argument("module")
    rev.add_argument("-m", "--message", required=True)
    rev.add_argument("--empty", action="store_true", help="不使用 autogenerate")
    purge = dbsub.add_parser("purge")
    purge.add_argument("module")
    # ★ 目录已经删了的补救口 —— 见 migrate.purge_module 的说明
    purge.add_argument("--revision", action="append", default=[],
                       help="目录已删时手工指定 revision，可重复")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    handlers = {
        "modules": cmd_modules,
        "schemas": cmd_schemas,
        "permissions": cmd_permissions,
        "capabilities": cmd_capabilities,
        "check": cmd_check,
        "db": cmd_db,
    }
    return handlers[args.command](args)


if __name__ == "__main__":
    raise SystemExit(main())
