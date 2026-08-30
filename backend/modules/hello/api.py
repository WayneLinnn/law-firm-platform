"""hello 模块的对外唯一出口。

其他模块只能 `from modules.hello.api import ...`，
或更推荐 `registry.api("hello")` —— 后者在模块被拆掉时能优雅降级。
"""
from modules.hello.service import create_note, get_note, list_notes

__all__ = ["create_note", "get_note", "list_notes"]
