# the one logger: only backend/core/logging.py touches the standard logging module, and only it and backend/cli print; main.py is held to the same
import ast
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
# the composition root, a module at the repository root held to the same rule as the package
ROOT_MODULE = ROOT / "main.py"
# the one module that owns the standard logging module and the output stream
LOGGER_MODULE = "backend/core/logging.py"
# the package of command-line entry points, whose print is the command's output and not a diagnostic
COMMANDS_PACKAGE = "backend/cli"


# each place a module reaches past the central logger: an import of logging, or a call of print
def bypasses(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import) and any(alias.name.split(".")[0] == "logging" for alias in node.names):
            found.append(f"line {node.lineno}: import logging")
        elif isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] == "logging":
            found.append(f"line {node.lineno}: from logging import")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "print":
            found.append(f"line {node.lineno}: print()")
    return found


# the bypasses a module at this path is not allowed: none for the logger module, the logging imports for a command, all for the rest
def refused_bypasses(path: str, source: str) -> list[str]:
    if path == LOGGER_MODULE:
        return []
    found = bypasses(source)
    if path.startswith(COMMANDS_PACKAGE + "/"):
        return [bypass for bypass in found if "logging" in bypass]
    return found


def test_no_module_of_the_service_logs_or_prints_past_the_central_logger():
    paths = sorted(BACKEND.rglob("*.py")) + [ROOT_MODULE]

    assert len(paths) > 40
    assert [
        f"{path.relative_to(ROOT)} {found}"
        for path in paths
        for found in refused_bypasses(path.relative_to(ROOT).as_posix(), path.read_text())
    ] == []


def test_the_logger_module_and_the_commands_package_exist_and_a_command_prints():
    commands = sorted((ROOT / COMMANDS_PACKAGE).glob("*.py"))

    assert (ROOT / LOGGER_MODULE).is_file()
    assert [path.name for path in commands if path.name != "__init__.py"] != []
    assert [found for path in commands for found in bypasses(path.read_text()) if "print" in found] != []


@pytest.mark.parametrize(
    "source",
    [
        "import logging",
        "import logging.handlers",
        "from logging import getLogger",
        "def run():\n    print('turn started')",
    ],
)
def test_a_bypass_of_the_central_logger_is_reported(source):
    assert bypasses(source) != []
    assert refused_bypasses("backend/application/agent_loop.py", source) != []


@pytest.mark.parametrize("source", ["from backend.core import logging as log", "from backend.core.logging import event", "log.event('x', outcome='ok')"])
def test_using_the_central_logger_is_not_a_bypass(source):
    assert bypasses(source) == []


@pytest.mark.parametrize("path", ["backend/infrastructure/postgres/reset.py", "main.py", "backend/climate.py", "backend/api/cli/run.py"])
def test_a_print_outside_the_commands_package_is_reported(path):
    assert refused_bypasses(path, "print('done')") != []


def test_a_command_may_print_its_output_but_not_import_logging():
    assert refused_bypasses("backend/cli/reset.py", "print('done')") == []
    assert refused_bypasses("backend/cli/reset.py", "import logging\nprint('done')") == ["line 1: import logging"]
