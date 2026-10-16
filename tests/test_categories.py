"""Every test file declares a category, so the CI jobs that pick tests by category run all of them."""

import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.platform

ROOT = Path(__file__).parent.parent


def test_every_test_file_declares_a_category():
    """Collects the suite and lists any test whose file lacks `pytestmark = pytest.mark.<category>` (see tests/conftest.py)."""
    done = subprocess.run(
        [sys.executable, "-m", "pytest", "tests", "--ignore=tests/e2e", "--collect-only", "-q", "-m", "uncategorized", "-p", "no:cacheprovider"],
        cwd=ROOT, capture_output=True, text=True, check=False,
    )
    lines = [line for line in done.stdout.splitlines() if line.startswith("tests/") and "::" in line]
    files = sorted({line.split("::")[0] for line in lines})
    assert files == [], f"add `pytestmark = pytest.mark.<category>` (accounts, platform, heroes, fights or social) to: {', '.join(files)}"
    assert done.returncode in (0, 5), done.stdout + done.stderr  # 5 = nothing selected, which is the goal
