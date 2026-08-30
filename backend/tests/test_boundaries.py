"""边界检查必须常绿 —— 这是「可拆卸」不腐化的守门人。"""
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parent.parent


def test_no_illegal_cross_module_imports():
    result = subprocess.run(
        [sys.executable, str(BACKEND / "scripts" / "check_boundaries.py")],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_checker_catches_violation(tmp_path):
    """反向验证：故意写一个越界 import，检查器必须抓到。"""
    sys.path.insert(0, str(BACKEND))
    from scripts.check_boundaries import check_file  # noqa: E402

    bad = BACKEND / "modules" / "hello" / "_tmp_bad.py"
    bad.write_text("from modules.other.service import boom\n", encoding="utf-8")
    try:
        violations = check_file(bad)
        assert violations, "越界 import 未被检出"
        assert "modules.other.api" in violations[0][2]
    finally:
        bad.unlink()
