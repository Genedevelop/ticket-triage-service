# the dependency rules of backend/ and of the composition root beside it, checked by reading the imports of every module
import ast
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"

# per layer: the packages its modules may import from this repository
LAYERS = {
    "backend.core": ("backend.core",),
    "backend.domain": ("backend.domain", "backend.core"),
    # each infrastructure package stands alone, except that the provider mocks keep their state in the store
    "backend.infrastructure.postgres": ("backend.infrastructure.postgres", "backend.domain", "backend.core"),
    "backend.infrastructure.llm": ("backend.infrastructure.llm", "backend.domain", "backend.core"),
    "backend.infrastructure.providers": ("backend.infrastructure.providers", "backend.infrastructure.postgres", "backend.domain", "backend.core"),
    "backend.infrastructure": ("backend.domain", "backend.core"),
    # the store is used directly; the model and the providers arrive as ports
    "backend.application": ("backend.application", "backend.domain", "backend.core", "backend.infrastructure.postgres"),
    "backend.api": ("backend.api", "backend.application", "backend.domain", "backend.core"),
    # command-line entry points construct infrastructure as the composition root does; no row above lists them, so nothing imports them
    "backend.cli": ("backend",),
}
# the composition root: the one module at the repository root, outside backend/, so no layer lists it and nothing under backend/ imports it
ROOT_MODULE = "main"
# it wires every layer together, and imports neither a command nor anything outside backend/
ROOT_MODULE_IMPORTS = ("backend.api", "backend.application", "backend.domain", "backend.core", "backend.infrastructure")
# the packages and the root module of this repository an import can name
FIRST_PARTY = ("backend", ROOT_MODULE, "evals", "tests")
# the one package under backend/ that may import each external library
OWNERS = {
    "fastapi": "backend.api",
    "starlette": "backend.api",
    "psycopg": "backend.infrastructure.postgres",
    "psycopg_pool": "backend.infrastructure.postgres",
    "openai": "backend.infrastructure.llm",
}
# external libraries any layer may import, because they do no input or output
SHARED_LIBRARIES = {"pydantic"}
# the standard-library modules the domain may import; only the import is checked, so datetime.now() and uuid.uuid4() in the domain would pass
DOMAIN_STDLIB = {
    "abc", "collections", "dataclasses", "datetime", "decimal", "enum", "functools", "itertools", "math", "re", "typing", "uuid",
}
# modules that import by a name built at run time, which reading the import statements cannot follow
DYNAMIC_IMPORTERS = {"importlib", "pkgutil", "runpy"}
# standard-library clients of a network or a database, which only infrastructure may import
STDLIB_CLIENTS = (
    "urllib.request", "http.client", "http.server", "socket", "socketserver", "ssl", "sqlite3", "dbm", "smtplib", "ftplib",
    "poplib", "imaplib", "xmlrpc",
)
# the one package under backend/ that may import them
STDLIB_CLIENTS_OWNER = "backend.infrastructure"


# whether a dotted name is the package or lies inside it
def within(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


# every dotted name a module imports, a relative import resolved against the module's own package
def imported_names(source: str, module: str, is_package: bool = False) -> set[str]:
    package = module if is_package else module.rpartition(".")[0]
    found = set()
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Import):
            found.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            base = node.module or ""
            if node.level:
                parent = ".".join(package.split(".")[: len(package.split(".")) - node.level + 1])
                base = f"{parent}.{base}" if base else parent
            found.update(f"{base}.{alias.name}" for alias in node.names)
    return found


# the layer a module belongs to, the most specific one when several contain it, or None when it belongs to none
def layer_of(module: str) -> str | None:
    return max((layer for layer in LAYERS if within(module, layer)), key=len, default=None)


# the modules that belong to no layer; the package backend itself is the root above them, held to root_violations instead
def unplaced(modules: list[str]) -> list[str]:
    return [module for module in modules if module != "backend" and layer_of(module) is None]


# every import of the package backend itself, which sits above every layer and so may hold no code that imports
def root_violations(module: str, names: set[str]) -> list[str]:
    return sorted(f"{module} imports {name}" for name in names) if module == "backend" else []


# the packages of this repository a module may import: the composition root's own list, or its layer's
def allowed_imports(module: str) -> tuple[str, ...]:
    return ROOT_MODULE_IMPORTS if module == ROOT_MODULE else LAYERS[layer_of(module)]


# the imports of this repository's packages that the module's layer does not allow
def layer_violations(module: str, names: set[str]) -> list[str]:
    allowed = allowed_imports(module)
    return sorted(
        f"{module} imports {name}"
        for name in names
        if any(within(name, package) for package in FIRST_PARTY) and not any(within(name, package) for package in allowed)
    )


# the imports of an external library made outside the package that owns it
def library_violations(module: str, names: set[str], library: str) -> list[str]:
    if within(module, OWNERS[library]):
        return []
    return sorted(f"{module} imports {name}" for name in names if within(name, library))


# the imports of an external library that no package owns and not every layer may use
def unowned_library_violations(module: str, names: set[str]) -> list[str]:
    known = sys.stdlib_module_names | SHARED_LIBRARIES | set(OWNERS) | set(FIRST_PARTY)
    return sorted(f"{module} imports {name}" for name in names if name.split(".")[0] not in known)


# the imports of a standard-library network or database client made outside infrastructure
def stdlib_client_violations(module: str, names: set[str]) -> list[str]:
    if within(module, STDLIB_CLIENTS_OWNER):
        return []
    return sorted(f"{module} imports {name}" for name in names if any(within(name, client) for client in STDLIB_CLIENTS))


# the imports of a domain module that are neither this repository's, nor pydantic, nor a listed standard-library module
def domain_violations(module: str, names: set[str]) -> list[str]:
    allowed = DOMAIN_STDLIB | SHARED_LIBRARIES | set(FIRST_PARTY)
    return sorted(f"{module} imports {name}" for name in names if name.split(".")[0] not in allowed)


# each place a module imports by a name built at run time
def dynamic_imports(source: str) -> list[str]:
    found = []
    for node in ast.walk(ast.parse(source)):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == "__import__":
            found.append(f"line {node.lineno}: __import__()")
        elif isinstance(node, ast.Import):
            found += [f"line {node.lineno}: import {alias.name}" for alias in node.names if alias.name.split(".")[0] in DYNAMIC_IMPORTERS]
        elif isinstance(node, ast.ImportFrom) and not node.level and (node.module or "").split(".")[0] in DYNAMIC_IMPORTERS:
            found.append(f"line {node.lineno}: from {node.module} import")
    return found


# every module under backend/ with the names it imports
def backend_modules() -> list[tuple[str, set[str]]]:
    modules = []
    for path in sorted(BACKEND.rglob("*.py")):
        parts = path.relative_to(ROOT).with_suffix("").parts
        is_package = parts[-1] == "__init__"
        module = ".".join(parts[:-1] if is_package else parts)
        modules.append((module, imported_names(path.read_text(), module, is_package)))
    return modules


# the composition root with the names it imports
def root_module() -> tuple[str, set[str]]:
    return ROOT_MODULE, imported_names((ROOT / f"{ROOT_MODULE}.py").read_text(), ROOT_MODULE)


# every module the rules on libraries hold: those under backend/ and the composition root
def service_modules() -> list[tuple[str, set[str]]]:
    return backend_modules() + [root_module()]


# the source files of those modules
def service_paths() -> list[Path]:
    return sorted(BACKEND.rglob("*.py")) + [ROOT / f"{ROOT_MODULE}.py"]


def test_every_module_belongs_to_a_layer():
    modules = [module for module, _ in backend_modules()]

    assert len(modules) > 40
    assert unplaced(modules) == []


def test_a_module_outside_every_layer_is_reported():
    assert unplaced(["backend", "backend.core.config", "backend.jobs.nightly", "backend.main", "backend.mainly", "backend.climate"]) == [
        "backend.jobs.nightly", "backend.main", "backend.mainly", "backend.climate",
    ]


def test_the_package_backend_itself_imports_nothing():
    roots = [(module, names) for module, names in backend_modules() if module == "backend"]

    assert len(roots) == 1
    assert [violation for module, names in roots for violation in root_violations(module, names)] == []


@pytest.mark.parametrize(
    "source", ["import evals.cases", "from tests import conftest", "from backend.core import config", "from . import main", "import os"]
)
def test_an_import_in_the_package_backend_itself_is_reported(source):
    assert root_violations("backend", imported_names(source, "backend", is_package=True)) != []


def test_a_module_belongs_to_its_most_specific_layer_whatever_the_order_of_the_table(monkeypatch):
    for ordered in (sorted(LAYERS), sorted(LAYERS, reverse=True)):
        monkeypatch.setattr(sys.modules[__name__], "LAYERS", {layer: LAYERS[layer] for layer in ordered})

        assert layer_of("backend.infrastructure.postgres.db") == "backend.infrastructure.postgres"
        assert layer_of("backend.infrastructure.providers") == "backend.infrastructure.providers"
        assert layer_of("backend.infrastructure") == "backend.infrastructure"
        assert layer_of("backend.application.tools.registry") == "backend.application"


@pytest.mark.parametrize("layer", sorted(LAYERS))
def test_a_layer_imports_only_the_packages_it_is_allowed(layer):
    modules = [(module, names) for module, names in backend_modules() if within(module, layer)]

    assert modules, f"{layer} has no modules"
    assert [violation for module, names in modules for violation in layer_violations(module, names)] == []


def test_the_composition_root_imports_only_the_layers_and_never_a_command_evals_or_tests():
    module, names = root_module()

    assert any(within(name, "backend") for name in names)
    assert layer_violations(module, names) == []


@pytest.mark.parametrize("library", sorted(OWNERS))
def test_each_external_library_is_imported_by_exactly_one_package(library):
    modules = service_modules()
    importing = [module for module, names in modules if any(within(name, library) for name in names)]

    assert [violation for module, names in modules for violation in library_violations(module, names, library)] == []
    assert importing, f"nothing imports {library}"


def test_no_module_of_the_service_imports_an_external_library_without_an_owner():
    assert [violation for module, names in service_modules() for violation in unowned_library_violations(module, names)] == []


def test_no_module_of_the_service_imports_by_a_name_built_at_run_time():
    paths = service_paths()

    assert len(paths) > 40
    assert [f"{path.relative_to(ROOT)} {found}" for path in paths for found in dynamic_imports(path.read_text())] == []


def test_the_domain_imports_only_the_listed_standard_library_modules_and_pydantic():
    modules = [(module, names) for module, names in backend_modules() if within(module, "backend.domain")]

    assert modules
    assert [violation for module, names in modules for violation in domain_violations(module, names)] == []


def test_no_module_outside_infrastructure_imports_a_standard_library_network_or_database_client():
    assert [violation for module, names in service_modules() for violation in stdlib_client_violations(module, names)] == []


@pytest.mark.parametrize(
    "module, source",
    [
        ("backend.core.config", "from backend.domain import policy"),
        ("backend.domain.rules", "from backend.application.tools.registry import REGISTRY"),
        ("backend.domain.ports", "import backend.infrastructure.postgres.db"),
        ("backend.infrastructure.llm.scripted", "from backend.application import decision"),
        ("backend.infrastructure.llm.openai_responses", "from backend.infrastructure.postgres import db"),
        ("backend.infrastructure.llm.scripted", "from backend.infrastructure.providers import mock_providers"),
        ("backend.infrastructure.postgres.actions", "from backend.infrastructure.providers.billing import MockBilling"),
        ("backend.infrastructure.postgres.kb", "from backend.infrastructure.llm import scripted"),
        ("backend.infrastructure.providers.billing", "from backend.api.problems import Problem"),
        ("backend.application.agent_loop", "from backend.infrastructure.llm.scripted import ScriptedLLM"),
        ("backend.application.tools.billing_lookup", "from backend.infrastructure.providers.billing import MockBilling"),
        ("backend.application.triage_turn", "from backend.infrastructure import providers"),
        ("backend.application.triage_turn", "from backend.api import problems"),
        ("backend.api.routes.health", "from backend.infrastructure.postgres import db"),
        ("backend.api.routes.health", "from ...infrastructure.postgres import db"),
        ("backend.application.model_input", "from evals import scenarios"),
        ("backend.application.triage_turn", "from backend.cli import reset"),
        ("backend.infrastructure.postgres.reset", "from backend.cli.reset import main"),
        ("backend.api.app", "import backend.cli.reset"),
        ("backend.api.app", "import main"),
        ("backend.cli.reset", "from main import create_app"),
        ("main", "from evals.scenarios import load"),
        ("main", "from tests.conftest import NOW"),
        ("main", "from backend.cli import reset"),
        ("backend.infrastructure.providers.sample_data", "from evals.cases import CASES"),
        ("backend.cli.reset", "from evals import smoke"),
        ("backend.cli.reset", "from tests import conftest"),
    ],
)
def test_a_forbidden_import_between_layers_is_reported(module, source):
    assert layer_violations(module, imported_names(source, module)) != []


@pytest.mark.parametrize(
    "module, source, library",
    [
        ("backend.application.agent_loop", "from fastapi import Request", "fastapi"),
        ("main", "from starlette.requests import Request", "starlette"),
        ("backend.infrastructure.providers.billing", "import psycopg", "psycopg"),
        ("backend.application.tools.kb_search", "from psycopg_pool import ConnectionPool", "psycopg_pool"),
        ("backend.infrastructure.postgres.kb", "import openai", "openai"),
    ],
)
def test_an_external_library_outside_its_package_is_reported(module, source, library):
    assert library_violations(module, imported_names(source, module), library) != []


@pytest.mark.parametrize("source", ["import httpx", "from sqlalchemy import select", "import requests.adapters"])
def test_an_external_library_without_an_owner_is_reported(source):
    assert unowned_library_violations("backend.application.agent_loop", imported_names(source, "backend.application.agent_loop")) != []


@pytest.mark.parametrize(
    "source",
    ["import importlib", "from importlib import import_module", "import pkgutil", "module = __import__('backend.infrastructure.llm')"],
)
def test_an_import_by_a_name_built_at_run_time_is_reported(source):
    assert dynamic_imports(source) != []


@pytest.mark.parametrize(
    "source",
    ["import os", "import time", "from pathlib import Path", "import socket", "import random", "import httpx", "from json import loads"],
)
def test_a_standard_library_module_that_is_not_listed_is_not_allowed_in_the_domain(source):
    assert domain_violations("backend.domain.policy", imported_names(source, "backend.domain.policy")) != []


@pytest.mark.parametrize("source", ["from datetime import datetime", "import uuid", "from pydantic import BaseModel", "from backend.core import errors"])
def test_a_listed_module_pydantic_and_this_repository_are_allowed_in_the_domain(source):
    assert domain_violations("backend.domain.policy", imported_names(source, "backend.domain.policy")) == []


@pytest.mark.parametrize(
    "module, source",
    [
        ("backend.application.tools.status_check", "import urllib.request"),
        ("backend.application.tools.status_check", "from urllib import request"),
        ("backend.application.tools.status_check", "from urllib.request import urlopen"),
        ("backend.application.agent_loop", "import http.client"),
        ("backend.application.agent_loop", "from http.client import HTTPSConnection"),
        ("backend.api.routes.health", "import socket"),
        ("backend.domain.policy", "import sqlite3"),
        ("backend.core.config", "import smtplib"),
        ("main", "from ftplib import FTP"),
        ("backend.cli.reset", "import sqlite3"),
    ],
)
def test_a_standard_library_network_or_database_client_outside_infrastructure_is_reported(module, source):
    assert stdlib_client_violations(module, imported_names(source, module)) != []


@pytest.mark.parametrize(
    "module, source",
    [
        ("backend.infrastructure.providers.billing", "import urllib.request"),
        ("backend.infrastructure.llm.openai_responses", "import http.client"),
        ("backend.application.agent_loop", "from urllib.parse import urlencode"),
        ("backend.application.agent_loop", "from http import HTTPStatus"),
    ],
)
def test_a_client_inside_infrastructure_and_a_module_that_only_parses_are_not_reported(module, source):
    assert stdlib_client_violations(module, imported_names(source, module)) == []
