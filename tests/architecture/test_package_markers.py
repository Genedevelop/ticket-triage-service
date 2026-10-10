# the package markers: every directory of modules under backend/ is a regular package whose __init__.py opens by saying what the package is
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
# one plain lower-case comment line
MARKER = re.compile(r"# [a-z].*")


# whether the source of a package marker opens with one plain lower-case comment line
def opens_with_a_comment(source: str) -> bool:
    return MARKER.fullmatch(source.split("\n", 1)[0]) is not None


# the directories at or under a root that hold a module and no package marker, which would make them namespace packages
def namespace_packages(root: Path) -> list[str]:
    directories = {path.parent for path in root.rglob("*.py")}
    return sorted(directory.relative_to(root.parent).as_posix() for directory in directories if not (directory / "__init__.py").is_file())


def test_every_package_marker_under_backend_opens_with_a_comment_line_saying_what_the_package_is():
    paths = sorted(BACKEND.rglob("__init__.py"))

    assert len(paths) > 10
    assert [path.relative_to(ROOT).as_posix() for path in paths if not opens_with_a_comment(path.read_text())] == []


@pytest.mark.parametrize(
    "source",
    [
        "",
        "\n# the store\n",
        "from backend.api.routes import health\n",
        "import os\n# the store\n",
        '"""The store."""\n',
        "#the store\n",
        "# The store.\n",
        "#\n",
    ],
)
def test_a_package_marker_that_does_not_open_with_a_plain_lower_case_comment_line_is_reported(source):
    assert not opens_with_a_comment(source)


@pytest.mark.parametrize("source", ["# the store\n", "# the store, the only package that imports psycopg\nfrom typing import Any\n"])
def test_a_package_marker_that_opens_with_a_comment_line_is_not_reported(source):
    assert opens_with_a_comment(source)


def test_every_directory_of_modules_under_backend_is_a_regular_package():
    assert namespace_packages(BACKEND) == []


def test_a_directory_of_modules_without_a_package_marker_is_reported(tmp_path):
    for module in ("backend/__init__.py", "backend/core/__init__.py", "backend/core/config.py", "backend/jobs/nightly.py"):
        (tmp_path / module).parent.mkdir(parents=True, exist_ok=True)
        (tmp_path / module).write_text("")

    assert namespace_packages(tmp_path / "backend") == ["backend/jobs"]
