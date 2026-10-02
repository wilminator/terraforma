"""Every test file belongs to a category, so the CI jobs that pick tests by category run all of them."""

from pathlib import Path

from .conftest import CATEGORY_OF_FILE


def test_every_test_file_has_a_category():
    files = {path.stem for path in Path(__file__).parent.glob("test_*.py")}
    assert files - set(CATEGORY_OF_FILE) == set(), "add the new test file to CATEGORIES in tests/conftest.py"
    assert set(CATEGORY_OF_FILE) - files == set(), "CATEGORIES lists a test file that does not exist"
