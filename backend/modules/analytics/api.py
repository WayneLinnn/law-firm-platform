"""analytics 模块的对外唯一出口。"""
from modules.analytics.service import catalog, collect, month_range, overview

__all__ = [
    "inbox", "URGENCY_LABEL", "KIND_LABEL","overview", "collect", "catalog", "month_range"]
