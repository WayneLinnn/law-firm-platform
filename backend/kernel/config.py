"""全局配置。所有环境变量统一前缀 LFP_。"""
from __future__ import annotations

from typing import List

from pydantic_settings import BaseSettings, SettingsConfigDict


#: 开发用的占位密钥。长度够 HS256（>=32 字节），但内容公开，生产必须替换。
DEV_JWT_SECRET = "dev-only-insecure-secret-change-me-in-production-32b"


def _split(value: str) -> List[str]:
    return [item.strip() for item in value.split(",") if item.strip()]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="LFP_",
        env_file=".env",
        extra="ignore",
        protected_namespaces=(),
    )

    app_name: str = "law-firm-platform"
    env: str = "dev"
    debug: bool = True

    # ---------- 数据库 ----------
    database_url: str = "postgresql+psycopg://lfp:lfp@localhost:5432/lfp"
    db_connect_timeout: int = 3

    # ---------- 模块开关 ----------
    # "*" = 启用全部已发现模块；黑名单优先级更高
    enabled_modules: str = "*"
    disabled_modules: str = ""
    # 启动时把模块清单写入 kernel.module_state（无数据库时静默跳过）
    record_module_state: bool = True

    # ---------- 模型（Step 2）----------
    # deepseek | fake（fake 仅用于测试与无 key 时的本地开发）
    model_backend: str = "deepseek"
    deepseek_api_key: str = ""
    deepseek_base_url: str = "https://api.deepseek.com"
    # 默认档位。难任务（定性、推理、文书起草）可在调用时传 model= 覆盖
    deepseek_model: str = "deepseek-v4-flash"
    deepseek_model_pro: str = "deepseek-v4-pro"
    model_timeout: int = 120
    model_max_retries: int = 2

    # ---------- 认证（Step 3）----------
    # 开发默认值。生产环境必须在 .env 里换成随机长串，
    # 否则 bootstrap 会拒绝启动（见 DEV_JWT_SECRET 检查）。
    # 生成： python -c "import secrets; print(secrets.token_urlsafe(48))"
    jwt_secret: str = DEV_JWT_SECRET
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 12 * 60

    # ---------- 法律检索服务（Step 8）----------
    # 独立进程，不在 Docker 里 —— 它还要大改（换数据源、扩充法条），
    # 现在容器化等于给一个还在动的东西做封装。
    # legal_rag | fake（fake 不联网，检索服务没起也能开发和跑 CI）
    retrieval_backend: str = "legal_rag"
    legal_rag_url: str = "http://127.0.0.1:8100"
    #: 纯检索是秒级的，但首次调用要加载 bge 模型，给宽一点
    legal_rag_timeout: int = 60
    #: 服务间令牌。本机开发留空；**上云必须设**，与检索服务的
    #: RAG_SERVICE_TOKEN 保持一致 —— 那个服务能查全部法条判例、还会花
    #: API 额度，端口暴露就是敞开的。
    legal_rag_token: str = ""

    # ---------- 流程编排（Step 9）----------
    # langgraph | fake（fake 是内存版，**不持久化**，只为让不关心流程的
    # 测试跑得动；真要跨进程恢复只有 langgraph 版算数）
    workflow_backend: str = "langgraph"
    #: 推进流程放不放后台线程。
    #: 建工流程一趟要跑抽要素+十几次检索+起草，实测几十秒 —— 同步会把 HTTP 吊死。
    #: 单测设 false 走同步：测试用的 session 在事务里，后台线程那条连接看不见它。
    workflow_async: bool = True

    # ---------- 记账 ----------
    #: 模型适配器按美元记账（厂商就是这么报价的），平台按人民币展示。
    #: **换算率只在这里定义一次** —— 散在各处必然对不上账。
    usd_cny_rate: float = 7.2

    # ---------- 对象存储（Step 2）----------
    # minio | fake
    storage_backend: str = "minio"
    minio_endpoint: str = "localhost:9000"
    minio_access_key: str = "lfp"
    minio_secret_key: str = "lfp123456"
    minio_bucket: str = "lfp-docs"
    minio_secure: bool = False

    @property
    def enabled_list(self) -> List[str]:
        return _split(self.enabled_modules)

    @property
    def disabled_list(self) -> List[str]:
        return _split(self.disabled_modules)

    def is_module_allowed(self, name: str) -> bool:
        if name in self.disabled_list:
            return False
        if self.enabled_modules.strip() == "*":
            return True
        return name in self.enabled_list


    def check_production_safety(self) -> None:
        """生产环境不允许使用开发默认值。启动时调用，早失败好过悄悄裸奔。"""
        if self.env.lower() in ("dev", "test", "local"):
            return
        problems = []
        if self.jwt_secret == DEV_JWT_SECRET:
            problems.append("LFP_JWT_SECRET 仍是开发默认值")
        if self.debug:
            problems.append("LFP_DEBUG 仍为 true")
        if problems:
            raise RuntimeError(
                "生产环境配置不安全：{0}。请在 .env 中修正。".format("；".join(problems))
            )


settings = Settings()
