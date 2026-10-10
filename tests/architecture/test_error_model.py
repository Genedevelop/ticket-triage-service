# the one error model, read from the code: every exception class is an AppError of one category, and each category has one HTTP status
import ast
import builtins
import importlib
import inspect
import pkgutil
import re
from pathlib import Path

import pytest

import backend as backend_package
from backend.api import problems
from backend.core import errors
from backend.core.errors import AppError

KEBAB = re.compile(r"[a-z]+(-[a-z]+)*")
BACKEND = Path(backend_package.__file__).resolve().parent
# the composition root, a module beside the package and held to the same rules
ROOT_MODULE = "main"
CATEGORIES = {
    cls for _, cls in inspect.getmembers(errors, inspect.isclass) if AppError in cls.__bases__ and cls.__module__ == errors.__name__
}


# every exception class defined in a module under backend/ or in the composition root
def exception_classes() -> list[type[BaseException]]:
    found = []
    for name in [info.name for info in pkgutil.walk_packages(backend_package.__path__, "backend.")] + [ROOT_MODULE]:
        module = importlib.import_module(name)
        found += [
            cls
            for _, cls in inspect.getmembers(module, inspect.isclass)
            if issubclass(cls, BaseException) and cls.__module__ == module.__name__
        ]
    return found


# the module or object each name is bound to by the import statements of a source, as far as importing it resolves
def imported_objects(source: str) -> dict[str, object]:
    found = {"builtins": builtins}
    for node in ast.walk(ast.parse(source)):
        try:
            if isinstance(node, ast.Import):
                for alias in node.names:
                    bound = importlib.import_module(alias.name if alias.asname else alias.name.split(".")[0])
                    importlib.import_module(alias.name)
                    found[alias.asname or alias.name.split(".")[0]] = bound
            elif isinstance(node, ast.ImportFrom) and not node.level:
                module = importlib.import_module(node.module)
                for alias in node.names:
                    if not hasattr(module, alias.name):
                        importlib.import_module(f"{node.module}.{alias.name}")
                    found[alias.asname or alias.name] = getattr(module, alias.name)
        except (ImportError, AttributeError):
            continue
    return found


# what a raised expression names, when the source alone says so: a built-in, or a dotted path from an imported name
def resolved(raised: ast.expr, imported: dict[str, object]) -> object | None:
    if isinstance(raised, ast.Name):
        return imported.get(raised.id, getattr(builtins, raised.id, None))
    if isinstance(raised, ast.Attribute):
        owner = resolved(raised.value, imported) if isinstance(raised.value, ast.Attribute) else imported.get(getattr(raised.value, "id", None))
        return getattr(owner, raised.attr, None) if owner is not None else None
    return None


# each place a module raises an exception class that is not an AppError: by a built-in name, through builtins, or through its own imports
def foreign_raises(source: str) -> list[str]:
    imported = imported_objects(source)
    found = []
    for node in ast.walk(ast.parse(source)):
        raised = node.exc if isinstance(node, ast.Raise) else None
        name = raised.func if isinstance(raised, ast.Call) else raised
        target = resolved(name, imported) if name is not None else None
        if isinstance(target, type) and issubclass(target, BaseException) and not issubclass(target, AppError):
            found.append(f"line {node.lineno}: raise {ast.unparse(name)}")
    return found


def test_no_module_of_the_service_raises_an_exception_class_that_is_not_an_app_error():
    paths = sorted(BACKEND.rglob("*.py")) + [BACKEND.parent / f"{ROOT_MODULE}.py"]

    assert len(paths) > 40
    assert [f"{path.relative_to(BACKEND.parent)} {found}" for path in paths for found in foreign_raises(path.read_text())] == []


@pytest.mark.parametrize(
    "source",
    [
        "raise RuntimeError('no')",
        "raise ValueError",
        "def run():\n    raise AssertionError('no')",
        "raise builtins.ValueError('no')",
        "import builtins as language\nraise language.KeyError('no')",
        "import json\nraise json.JSONDecodeError('no', 'doc', 0)",
        "from json import JSONDecodeError\nraise JSONDecodeError('no', 'doc', 0)",
        "from json import JSONDecodeError as Undecodable\nraise Undecodable",
        "import json.decoder\nraise json.decoder.JSONDecodeError('no', 'doc', 0)",
        "import concurrent.futures as futures\nraise futures.TimeoutError()",
        "from pydantic import ValidationError\nraise ValidationError",
    ],
)
def test_a_raise_of_an_exception_class_that_is_not_an_app_error_is_reported(source):
    assert foreign_raises(source) != []


@pytest.mark.parametrize(
    "source",
    [
        "raise",
        "raise error",
        "raise Lost(conversation_id)",
        "raise ToolError.from_provider(error) from error",
        "from backend.core import errors\nraise errors.Internal('bug')",
        "from backend.core.errors import Lost\nraise Lost(conversation_id)",
        "import backend.core.errors\nraise backend.core.errors.Conflict('taken')",
        "from backend.application.tools.tool import ToolError\nraise ToolError.from_provider(error) from error",
    ],
)
def test_a_raise_of_an_app_error_or_a_caught_exception_is_not_reported(source):
    assert foreign_raises(source) == []


@pytest.mark.parametrize("source", ["failure = ValueError\nraise failure('no')", "raise make_error()", "import json\nraise vars(json)['JSONDecodeError']"])
def test_a_raise_the_source_alone_does_not_resolve_is_not_reported(source):
    assert foreign_raises(source) == []


def test_every_exception_class_of_the_service_is_an_app_error():
    classes = exception_classes()

    assert len(classes) > len(CATEGORIES)
    assert [cls.__qualname__ for cls in classes if not issubclass(cls, AppError)] == []


def test_every_app_error_but_the_base_belongs_to_exactly_one_category():
    misplaced = [
        cls.__qualname__
        for cls in exception_classes()
        if cls is not AppError and len(CATEGORIES & set(cls.__mro__)) != 1
    ]

    assert misplaced == []


def test_no_two_exception_classes_share_a_name():
    names = [cls.__name__ for cls in exception_classes()]

    assert sorted(names) == sorted(set(names))


def test_every_error_class_has_a_kebab_case_code_and_a_title():
    for cls in exception_classes():
        assert KEBAB.fullmatch(cls.code), cls.__qualname__
        assert cls.title, cls.__qualname__


def test_every_category_maps_to_exactly_one_status():
    assert set(problems.STATUS) == CATEGORIES
    assert all(isinstance(status, int) and 400 <= status <= 599 for status in problems.STATUS.values())
