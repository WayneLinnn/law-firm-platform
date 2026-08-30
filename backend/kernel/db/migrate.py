"""迁移管理。Alembic 配置在运行时根据已装载模块动态拼装。

每个模块 = 一条独立 branch：
  - 新模块第一条迁移 down_revision=None, branch_labels=("<模块名>",)
  - 之后的迁移接在 "<模块名>@head" 上
  - 升级一律 `upgrade heads`（复数，把所有分支推到最新）
"""
from __future__ import annotations

import os
import re
from pathlib import Path
from typing import List, Optional

from alembic import command
from alembic.config import Config

from kernel.config import settings
from kernel.registry import registry

MIGRATIONS_DIR = Path(__file__).resolve().parent / "migrations"


def module_versions_path(module: str) -> Path:
    import importlib

    pkg = importlib.import_module("modules.{0}".format(module))
    return Path(pkg.__file__).resolve().parent / "migrations" / "versions"


def build_config() -> Config:
    registry.load()
    cfg = Config()
    cfg.set_main_option("script_location", str(MIGRATIONS_DIR))
    cfg.set_main_option("version_path_separator", "os")
    cfg.set_main_option("version_locations", os.pathsep.join(registry.migration_locations()))
    cfg.set_main_option("sqlalchemy.url", settings.database_url)
    return cfg


def upgrade(target: str = "heads") -> None:
    command.upgrade(build_config(), target)


def downgrade(target: str) -> None:
    command.downgrade(build_config(), target)


def current() -> None:
    command.current(build_config(), verbose=True)


def heads() -> None:
    command.heads(build_config(), verbose=True)


def history() -> None:
    command.history(build_config(), verbose=True)


def new_revision(module: str, message: str, autogenerate: bool = True) -> None:
    """为指定模块生成一条迁移，落在该模块自己的 migrations/versions 里。"""
    if not registry.has(module):
        raise SystemExit("模块 {0} 未启用或不存在".format(module))

    path = module_versions_path(module)
    path.mkdir(parents=True, exist_ok=True)
    existing = [p for p in path.glob("*.py") if not p.name.startswith("__")]

    cfg = build_config()
    branch_label: Optional[str] = None
    head = "{0}@head".format(module)
    if not existing:
        branch_label = module
        head = "base"

    # ★ 把 autogenerate 的比对范围锁死在本模块自己的 schema 内。
    #   不锁的话 Alembic 会拿全库和全量 metadata 比对，把其他模块的表
    #   当成"多余的"而生成 drop_table —— 一次 revision 能删掉整个库。
    previous = os.environ.get("LFP_MIGRATION_SCHEMA")
    os.environ["LFP_MIGRATION_SCHEMA"] = module
    try:
        command.revision(
            cfg,
            message=message,
            autogenerate=autogenerate,
            version_path=str(path),
            head=head,
            branch_label=branch_label,
        )
    finally:
        if previous is None:
            os.environ.pop("LFP_MIGRATION_SCHEMA", None)
        else:
            os.environ["LFP_MIGRATION_SCHEMA"] = previous


_REVISION_RE = re.compile(
    r"""^revision(?::\s*str)?\s*=\s*['"]([^'"]+)['"]""", re.M
)


def _revisions_in(path) -> list:
    """从迁移文件里**读出**真正的 revision，不要从文件名去猜。

    ★ 这里原来写的是 `stem.split("_")[0] + "_" + stem.split("_")[1]`，
      对 `hello_0001_init.py` 恰好蒙对（revision 真叫 hello_0001），
      但 alembic 自动生成的文件名是 `6d1af4734562_workflow_runs_and_steps.py`，
      猜出来的是 `6d1af4734562_workflow` —— 删不掉任何记录。

      后果很隐蔽：purge 把 schema DROP 了，版本记录却还留着，
      于是 `db upgrade` 认为"早就升过了"直接跳过 —— **模块拆得掉、装不回来**，
      "可拆卸"这条铁律实际是破的。
    """
    revs = []
    for f in sorted(path.glob("*.py")):
        if f.name.startswith("__"):
            continue
        m = _REVISION_RE.search(f.read_text(encoding="utf-8"))
        if m:
            revs.append(m.group(1))
    return revs


def purge_module(module: str, revisions: Optional[List[str]] = None) -> None:
    """彻底拆除一个模块的数据痕迹：DROP SCHEMA + 清掉它的 alembic 分支记录。

    ★ **必须在删掉模块目录之前跑。**

      这个函数要**读模块的迁移文件**才知道该删哪些 revision。
      目录先删了，它就只能 DROP 掉 schema、把版本记录留在库里 ——
      然后下一次 `db upgrade` 去找一个不存在的脚本，报
      `Can't locate revision identified by 'xxx'`。
      **而且是升级时才炸，不是启动时**，老库升级才暴露。

      2026-08-28 拆 `time` 模块时就是这么踩的：目录先删了，
      于是只能手工写 SQL 补救。这条检查就是那次加的。

    ★ 目录已经没了也有救：把 revision 直接传进来
      （`git show HEAD~1:modules/<name>/migrations/versions/*.py | grep revision`，
      或者从报错信息里抄那一串）。

    :param revisions: 目录已删时手工指定要清掉的 revision
    """
    from sqlalchemy import create_engine, text

    revs = list(revisions or [])
    if not revs:
        try:
            path = module_versions_path(module)
        except ImportError:
            path = None
        if path is not None and path.exists():
            revs = _revisions_in(path)
        else:
            # ★ **不静默跳过。** 静默的后果是"拆得掉、装不回来"，
            #   而那要等到下一次 db upgrade 才发现。
            raise RuntimeError(
                "找不到模块 {0} 的迁移目录 —— 多半是**先删了目录才来 purge**。\n"
                "\n"
                "purge 必须在删目录之前跑，否则 schema 掉了、版本记录还在，\n"
                "下次 db upgrade 会报 Can't locate revision。\n"
                "\n"
                "补救：把该模块的 revision 传进来，例如\n"
                "    db purge {0} --revision b6a9c6d018e5\n"
                "revision 可以从 git 里翻出来，或者直接抄报错信息里那一串。"
                .format(module)
            )

    engine = create_engine(settings.database_url, future=True)
    with engine.begin() as conn:
        conn.execute(text('DROP SCHEMA IF EXISTS "{0}" CASCADE'.format(module)))
        for rev in revs:
            conn.execute(
                text("DELETE FROM kernel.alembic_version WHERE version_num = :r"),
                {"r": rev},
            )
    print("已清除模块 {0} 的 schema，以及 {1} 条迁移记录：{2}".format(
        module, len(revs), "、".join(revs) or "（无）"))
