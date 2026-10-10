import socket

import pytest

from tests.e2e import conftest as e2e
from tests.e2e.conftest import HOST, PORT_TAKEN, Launcher, free_port

FACTORY_THAT_DOES_NOT_EXIST = "tests.e2e.server:no_such_factory"


@pytest.fixture()
def launcher(empty_database, tmp_path):
    started = Launcher(empty_database, tmp_path)
    try:
        yield started
    finally:
        started.reap_all()


# a port another socket already holds, so a server told to bind it exits with the address in use
@pytest.fixture()
def taken_port():
    with socket.socket() as holder:
        holder.bind((HOST, 0))
        yield holder.getsockname()[1]


def test_a_server_that_lost_its_port_is_started_once_more_on_another_and_serves(launcher, taken_port, monkeypatch):
    chosen = iter([taken_port, free_port()])
    monkeypatch.setattr(e2e, "free_port", lambda: next(chosen))

    server = launcher.start()

    lost, served = launcher.servers
    assert server is served
    assert lost.process.returncode not in (None, 0)
    assert PORT_TAKEN in lost.log()
    assert PORT_TAKEN not in served.log()
    assert served.process.poll() is None
    assert served.http.get("/healthz").json() == {"status": "ok", "database": True}


def test_a_server_that_loses_its_port_twice_is_not_started_a_third_time(launcher, taken_port, monkeypatch):
    monkeypatch.setattr(e2e, "free_port", lambda: taken_port)

    with pytest.raises(AssertionError, match="the server exited with 3 before it answered") as failure:
        launcher.start()

    assert PORT_TAKEN in str(failure.value)
    assert len(launcher.servers) == 2


def test_a_server_that_exits_early_for_any_other_reason_is_not_started_again(launcher):
    with pytest.raises(AssertionError, match="the server exited with 3 before it answered") as failure:
        launcher.start(factory=FACTORY_THAT_DOES_NOT_EXIST)

    (failed,) = launcher.servers
    assert PORT_TAKEN not in failed.log()
    assert "no_such_factory" in str(failure.value)
