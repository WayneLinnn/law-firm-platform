"""外部实现。只有 kernel/bootstrap.py 可以 import 这里。

adapters/
    model/       DeepSeek、私有化推理服务
    storage/     MinIO / OSS
    retrieval/   你已有的 LangGraph 检索系统
    workflow/    LangGraph 编排
"""
