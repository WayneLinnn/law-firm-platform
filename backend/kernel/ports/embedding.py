"""向量化契约 —— 与 ModelPort 刻意分开。

两个原因：
  1. **DeepSeek 不提供 embeddings 接口**（官方 API Reference 里没有该端点）
  2. **D2：向量必须本地跑** —— 案情文本不出境是律师保密义务的要点，
     已有检索系统用的就是本地 bge-base-zh-v1.5（768 维，免费）

所以对话和向量本来就是两个不同的提供方，混在一个 Port 里会逼出一个
"抛 NotImplementedError 的假接口"。

【Step 2 只定义契约，不绑定实现】
实现落在 Step 8（复用检索服务里已有的 bge）或 Step 20（租户私有知识库入 pgvector）。
"""
from __future__ import annotations

from typing import List

try:
    from typing import Protocol, runtime_checkable
except ImportError:  # pragma: no cover
    from typing_extensions import Protocol, runtime_checkable  # type: ignore


@runtime_checkable
class EmbeddingPort(Protocol):
    #: 向量维度，建库时要与 pgvector 列定义一致
    dimensions: int

    def embed(self, texts: List[str]) -> List[List[float]]:
        """批量向量化。返回值已归一化，点积即余弦相似度。"""
        ...

    def embed_query(self, text: str) -> List[float]:
        """检索侧的单条向量化（有些模型对 query 和 doc 用不同前缀）。"""
        ...
