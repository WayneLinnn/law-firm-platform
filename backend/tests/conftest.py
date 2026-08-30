import os
import sys
from pathlib import Path

# 测试不连数据库
os.environ.setdefault("LFP_RECORD_MODULE_STATE", "false")
os.environ.setdefault("LFP_ENV", "test")

BACKEND = Path(__file__).resolve().parent.parent
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))
