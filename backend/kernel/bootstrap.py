"""启动装配：绑定 ports 实现 + 装载模块。

**这是全项目唯一可以 import adapters 的文件**（边界检查器对它开了白名单）。
业务模块永远只通过 container.get(...) 拿能力。
"""
from __future__ import annotations

import logging

from kernel.config import settings
from kernel.container import container
from kernel.ports.events import NullEventBus
from kernel.registry import registry

logger = logging.getLogger(__name__)


def bind_adapters() -> None:
    """把外部实现挂到容器上。

    绑定是**惰性**的：container 只在第一次 get() 时才真正构造实例。
    所以没有 API Key、没有 MinIO 时应用照样能启动，直到真正用到才报错。
    """
    # events：预留接口，先用空实现，业务侧可以放心调用
    container.bind("events", NullEventBus)

    # model：DeepSeek（LFP_MODEL_BACKEND=fake 时用假实现，无 key 也能开发）
    def _model():
        from adapters.model.deepseek import build_model_adapter

        return build_model_adapter(settings)

    container.bind("model", _model)

    # storage：MinIO（LFP_STORAGE_BACKEND=fake 时用内存实现）
    def _storage():
        from adapters.storage.minio_storage import build_storage_adapter

        return build_storage_adapter(settings)

    container.bind("storage", _storage)

    # retrieval / legal_qa：**同一个检索服务同时实现两个 Port**
    #   RetrievalPort  纯检索：0 成本、秒级、结果确定、可缓存
    #   LegalQAPort    问答/要素/对比：有成本、任务化
    # 对面是独立进程（langchain-legal-RAG 的 src/service.py），不是 in-process
    # import —— 那个库要大改，HTTP 契约把平台和它的内部结构隔开。
    def _legal_rag():
        from adapters.retrieval.legal_rag_http import build_retrieval_adapter

        return build_retrieval_adapter(settings)

    container.bind("retrieval", _legal_rag)
    container.bind("legal_qa", _legal_rag)

    # workflow：案件流程编排（LangGraph + Postgres checkpointer）
    #
    # ★ 这是**第二张图**，和 RAG 那张检索图完全不同的东西：
    #     检索图  问一次答一次，秒级，无状态，一律 human=False
    #     流程图  一个案子跑几个月，必须落盘，**人工确认只在这一层**（C1）
    # checkpoint 表由 LangGraph 自己在 workflow schema 里建，不写 Alembic 迁移
    # —— 那是库的私有结构，手写迁移必然和库版本打架。
    def _workflow():
        from adapters.workflow.langgraph_engine import build_workflow_adapter

        return build_workflow_adapter(settings)

    container.bind("workflow", _workflow)

    # ---- 以下为后续步骤的挂载点，现在留空 ----
    # Step 8.5: container.bind("embedding", ...)  EmbeddingPort  本地 bge（D2）


def bootstrap() -> None:
    logging.basicConfig(
        level=logging.DEBUG if settings.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings.check_production_safety()
    bind_adapters()
    registry.load()
    logger.info("bootstrap 完成：%s", registry.order)
