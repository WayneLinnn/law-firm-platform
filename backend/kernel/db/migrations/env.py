"""Alembic 环境。每个模块是一条独立分支（branch），互不牵连。

删掉一个模块目录，它的整条迁移分支随之消失，不会打断其他模块 —— 这是
「可拆卸」在数据库层面的保证。升级用 `upgrade heads`（复数）。

★ 关键：autogenerate 时必须**只看当前模块自己的 schema**。
   否则 Alembic 会拿全库和全量 metadata 比对，把别的模块的表当成"多余的"
   生成 drop_table —— 一次 revision 就能把整个库删干净。
   由 kernel/db/migrate.py 通过 LFP_MIGRATION_SCHEMA 指定范围。
"""
from __future__ import annotations

import os

from alembic import context
from sqlalchemy import create_engine, text

from kernel.bootstrap import bootstrap
from kernel.config import settings
from kernel.db.base import Base
from kernel.registry import registry

bootstrap()
import kernel.db.models  # noqa: F401  内核自己的表也要进 metadata
registry.import_models()

config = context.config
target_metadata = Base.metadata

#: 只对这个 schema 做 autogenerate 比对；留空表示全部（仅 upgrade 时用得到）
TARGET_SCHEMA = os.environ.get("LFP_MIGRATION_SCHEMA", "").strip()


def _url() -> str:
    return config.get_main_option("sqlalchemy.url") or settings.database_url


def include_name(name, type_, parent_names):
    """把比对范围限制在目标 schema 内。"""
    if not TARGET_SCHEMA:
        return True
    if type_ == "schema":
        return name == TARGET_SCHEMA
    return True


#: **由外部引擎自己建、自己升级的表。autogenerate 必须当它们不存在。**
#:
#: 这些表不在我们的 ORM metadata 里，autogenerate 一看见就会判定
#: "库里有、代码里没有" → 生成 op.drop_table()。对 checkpoints 来说，
#: 那等于**把全所在跑的案件流程一次抹掉**（Step 1 踩过同款：
#: 给 identity 生成迁移时差点 drop 掉 kernel.module_state）。
#:
#: 为什么不给它们写 ORM 模型：结构由 langgraph 决定、随版本变，
#: 我们写一份就等于跟库的迁移打架。让库自己管，我们只负责别删它。
ENGINE_OWNED_TABLES = {
    # langgraph-checkpoint-postgres
    "checkpoints",
    "checkpoint_writes",
    "checkpoint_blobs",
    "checkpoint_migrations",
}


def include_object(obj, name, type_, reflected, compare_to):
    if not TARGET_SCHEMA:
        return True
    if type_ == "table" and name in ENGINE_OWNED_TABLES:
        return False
    # 索引也要跟着排除，否则会生成 drop_index
    parent = getattr(obj, "table", None)
    if parent is not None and parent.name in ENGINE_OWNED_TABLES:
        return False
    schema = getattr(obj, "schema", None)
    if schema is None and parent is not None:
        schema = parent.schema
    return schema == TARGET_SCHEMA


def ensure_schemas(connection) -> None:
    for schema in registry.schemas():
        connection.execute(text('CREATE SCHEMA IF NOT EXISTS "{0}"'.format(schema)))


def _common_opts() -> dict:
    return {
        "target_metadata": target_metadata,
        "include_schemas": True,
        "version_table_schema": "kernel",
        "compare_type": True,
        "include_name": include_name,
        "include_object": include_object,
    }


def run_migrations_offline() -> None:
    context.configure(url=_url(), literal_binds=True, **_common_opts())
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    engine = create_engine(_url(), future=True)
    with engine.connect() as connection:
        ensure_schemas(connection)
        connection.commit()
        context.configure(connection=connection, **_common_opts())
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
