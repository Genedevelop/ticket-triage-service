# fixtures of the end-to-end tests: the application served by uvicorn as a separate process on a free local port
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Callable

import httpx
import pytest

from tests.conftest import truncate_every_table
from tests.e2e.server import BILLING_FAULTS_KEY, MODEL_SCRIPT_KEY

ROOT = Path(__file__).resolve().parents[2]
SCRIPTED_FACTORY = "tests.e2e.server:create_scripted_app"
REAL_FACTORY = "main:create_app"
HOST = "127.0.0.1"
START_DEADLINE_S = 30.0
STOP_DEADLINE_S = 15.0
WAIT_DEADLINE_S = 15.0
POLL_INTERVAL_S = 0.02
REQUEST_TIMEOUT_S = 30.0
# what uvicorn writes once the lifespan's shut-down half has run
SHUTDOWN_COMPLETE = "Application shutdown complete."
# what uvicorn writes when another process bound the port first
PORT_TAKEN = "address already in use"


# a port nothing listens on at this moment, chosen by the operating system
def free_port() -> int:
    with socket.socket() as listener:
        listener.bind((HOST, 0))
        return listener.getsockname()[1]


# poll until the condition holds, failing with what was waited for once the deadline has passed
def wait_until(condition: Callable[[], Any], what: str, deadline_s: float = WAIT_DEADLINE_S) -> Any:
    deadline = time.monotonic() + deadline_s
    while time.monotonic() < deadline:
        found = condition()
        if found:
            return found
        time.sleep(POLL_INTERVAL_S)
    raise AssertionError(f"timed out after {deadline_s}s waiting for {what}")


# one served application: its process, its address and everything it wrote
class Server:
    def __init__(self, process: subprocess.Popen, port: int, log_path: Path) -> None:
        self.process = process
        self.log_path = log_path
        self.http = httpx.Client(base_url=f"http://{HOST}:{port}", timeout=REQUEST_TIMEOUT_S, trust_env=False)

    def log(self) -> str:
        return self.log_path.read_text()

    # the JSON lines of the service's own logger, without what uvicorn wrote
    def events(self) -> list[dict[str, Any]]:
        return [json.loads(line) for line in self.log().splitlines() if line.startswith("{")]

    def is_healthy(self) -> bool:
        if self.process.poll() is not None:
            raise AssertionError(f"the server exited with {self.process.returncode} before it answered:\n{self.log()}")
        try:
            return self.http.get("/healthz").status_code == 200
        except httpx.TransportError:
            return False

    # whether the server exited because something else took its port between the choice and the bind
    def has_lost_its_port(self) -> bool:
        return self.process.poll() is not None and PORT_TAKEN in self.log()

    # ask the server to stop and wait until it has, as an orchestrator does
    def stop(self) -> int:
        self.process.terminate()
        return self.process.wait(timeout=STOP_DEADLINE_S)

    # end the process at once, with no chance to finish anything
    def kill(self) -> None:
        self.process.kill()
        self.process.wait(timeout=STOP_DEADLINE_S)

    # leave no process behind: stop one that still runs, and end it at once when it does not stop in time
    def reap(self) -> None:
        self.http.close()
        if self.process.poll() is not None:
            return
        try:
            self.stop()
        except subprocess.TimeoutExpired:
            self.kill()


# starts servers on one database and reaps every one of them, however the test ended
class Launcher:
    def __init__(self, database_url: str, log_directory: Path) -> None:
        self.database_url = database_url
        self.log_directory = log_directory
        self.servers: list[Server] = []

    # a server that lost its port to another process is started once more, on a port chosen again
    def start(
        self, script: tuple[dict[str, Any], ...] = (), billing_faults: tuple[str, ...] = (), factory: str = SCRIPTED_FACTORY
    ) -> Server:
        server = self.spawn(script, billing_faults, factory)
        try:
            wait_until(server.is_healthy, "the server to answer /healthz", START_DEADLINE_S)
        except AssertionError:
            if not server.has_lost_its_port():
                raise
            server = self.spawn(script, billing_faults, factory)
            wait_until(server.is_healthy, "the server to answer /healthz", START_DEADLINE_S)
        return server

    def spawn(self, script: tuple[dict[str, Any], ...], billing_faults: tuple[str, ...], factory: str) -> Server:
        port = free_port()
        log_path = self.log_directory / f"server-{len(self.servers)}.log"
        environment = {
            **os.environ,
            "DATABASE_URL": self.database_url,
            "OPENAI_API_KEY": "",
            MODEL_SCRIPT_KEY: json.dumps(script),
            BILLING_FAULTS_KEY: json.dumps(billing_faults),
        }
        command = [sys.executable, "-m", "uvicorn", factory, "--factory", "--host", HOST, "--port", str(port)]
        with log_path.open("w") as log_file:
            process = subprocess.Popen(command, cwd=ROOT, env=environment, stdout=log_file, stderr=subprocess.STDOUT)
        server = Server(process, port, log_path)
        self.servers.append(server)
        return server

    def reap_all(self) -> None:
        for server in self.servers:
            server.reap()


# start a server on the emptied test database and wait until it answers: serve(script, billing_faults, factory)
@pytest.fixture()
def serve(empty_database: str, tmp_path: Path):
    launcher = Launcher(empty_database, tmp_path)
    try:
        yield launcher.start
    finally:
        launcher.reap_all()


# one server with no script, shared by the tests of a module that never reach the model or reach it only to fail
@pytest.fixture(scope="module")
def unscripted_server(database_url: str, tmp_path_factory: pytest.TempPathFactory):
    truncate_every_table(database_url)
    launcher = Launcher(database_url, tmp_path_factory.mktemp("unscripted"))
    try:
        yield launcher.start()
    finally:
        launcher.reap_all()
