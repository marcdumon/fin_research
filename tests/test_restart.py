"""The Restart server button: the route starts the same command again."""

from fastapi.testclient import TestClient

from fin_research.web import api


def test_restart_starts_the_same_command_again(monkeypatch) -> None:
    started = []
    monkeypatch.setattr(api.time, 'sleep', lambda seconds: None)
    monkeypatch.setattr(api.os, 'execv', lambda program, arguments: started.append((program, arguments)))

    class NowThread:
        """Runs the target at once, so the test needs no waiting."""

        def __init__(self, target, daemon) -> None:
            self.target = target

        def start(self) -> None:
            self.target()

    monkeypatch.setattr(api.threading, 'Thread', NowThread)
    reply = TestClient(api.app).post('/api/restart')
    assert reply.status_code == 200
    assert started == [(api.sys.executable, api.sys.orig_argv)]
