"""App lifecycle: SIGTERM stops cleanly (exit code 0), parallel server stop.

All HA/Shelly/vendor traffic is mocked (conftest.FakeNet) or goes to local test servers."""

import asyncio
import json
import os
import signal

import data_paths
import main
import manager
import pytest
from conftest import OPTIONS


@pytest.fixture
def app_env(net, monkeypatch, tmp_path):
    """Options file, free ports, no start delay, fake registry and notifications."""
    options = tmp_path / "options.json"
    options.write_text(json.dumps(OPTIONS))
    monkeypatch.setattr(main, "OPTIONS_FILE", options)
    data_paths.SETTINGS_FILE.write_text(json.dumps(OPTIONS))  # options stored by the app
    monkeypatch.setattr(main, "UI_PORT", 0)  # any free port
    monkeypatch.setattr(main, "DOWNLOAD_PORT", 0)
    monkeypatch.setattr(main, "STARTUP_DELAY_S", 0)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "t")

    async def registry(_self):
        return net.registry()

    async def notify(_self, *_args):
        return True

    monkeypatch.setattr(manager.Manager, "_registry", registry)
    monkeypatch.setattr(manager.Manager, "notify", notify)
    return net


async def _send_sigterm():
    """SIGTERM to this process – only once the app has installed its handler."""
    for _ in range(100):
        if signal.getsignal(signal.SIGTERM) not in (signal.SIG_DFL, None):
            break
        await asyncio.sleep(0.01)
    else:
        pytest.fail("No SIGTERM handler installed – the app would be killed (exit code 143)")
    os.kill(os.getpid(), signal.SIGTERM)


async def test_sigterm_idle_returns_normally(app_env):
    """Idle app: SIGTERM ends main() without an exception (= exit code 0)."""
    task = asyncio.create_task(main.main())
    await asyncio.sleep(0.2)
    await _send_sigterm()
    assert await asyncio.wait_for(task, 5) is None


async def test_sigterm_cancels_running_check_and_saves_state(app_env, monkeypatch):
    """SIGTERM during an automatic check: the check is cancelled, no device stays
    'checking', the state is saved, main() returns normally."""
    monkeypatch.setattr(manager.Manager, "scan_due_in", lambda _self, _now: 0)
    data_paths.STATE_FILE.write_text(json.dumps({"first_start_open": False}))  # not a fresh installation
    app_env.gate = CountingEvent()  # device queries block: the check keeps running
    task = asyncio.create_task(main.main())
    for _ in range(250):  # wait until the check is querying the devices
        await asyncio.sleep(0.02)
        if app_env.gate.waiting:
            break
    else:
        pytest.fail("The automatic check did not start")
    await _send_sigterm()
    assert await asyncio.wait_for(task, 5) is None
    state = json.loads(data_paths.STATE_FILE.read_text())
    assert state["last_scan"]  # written by the check's finally block
    assert "activity" not in data_paths.STATE_FILE.read_text()


async def test_fresh_installation_waits_for_the_first_start_answer(app_env, monkeypatch):
    """No state file (fresh installation): one notification, no automatic check until
    the import question is answered; "Skip" lets the due check start."""
    monkeypatch.setattr(manager.Manager, "scan_due_in", lambda _self, _now: 0)
    sent = []
    created = []

    async def notify(_self, *args):
        sent.append(args)
        return True

    real_init = manager.Manager.__init__

    def init(self, *args, **kwargs):
        real_init(self, *args, **kwargs)
        created.append(self)

    monkeypatch.setattr(manager.Manager, "notify", notify)
    monkeypatch.setattr(manager.Manager, "__init__", init)
    app_env.gate = CountingEvent()
    task = asyncio.create_task(main.main())
    await asyncio.sleep(0.5)
    assert created and created[0].first_start_open
    assert not app_env.gate.waiting  # no check started
    assert [a[2] for a in sent] == ["shelly_firmware_manager_first_start"]
    created[0].skip_first_start("tester")
    for _ in range(250):
        await asyncio.sleep(0.02)
        if app_env.gate.waiting:
            break
    else:
        pytest.fail("The check did not start after the question was answered")
    await _send_sigterm()
    assert await asyncio.wait_for(task, 5) is None
    state = json.loads(data_paths.STATE_FILE.read_text())
    assert state["first_start_open"] is False and state["first_start_notified"] is True


class CountingEvent(asyncio.Event):
    """asyncio.Event that counts the coroutines currently waiting for it."""

    def __init__(self):
        """Start with no waiters."""
        super().__init__()
        self.waiting = 0

    async def wait(self):
        """Wait like asyncio.Event and keep the count of waiters."""
        self.waiting += 1
        try:
            return await super().wait()
        finally:
            self.waiting -= 1


async def test_scheduler_error_is_still_reported(app_env, monkeypatch):
    """An unexpected scheduler error is not hidden by the clean shutdown."""

    async def broken(_mgr):
        raise RuntimeError("scheduler broke")

    monkeypatch.setattr(main, "run_schedule", broken)
    with pytest.raises(RuntimeError, match="scheduler broke"):
        await asyncio.wait_for(main.main(), 5)


async def test_servers_are_stopped_in_parallel(app_env, monkeypatch):
    from aiohttp import web as aioweb

    running, overlap = 0, []
    original = aioweb.AppRunner.cleanup

    async def slow_cleanup(self):
        nonlocal running
        running += 1
        overlap.append(running)
        await asyncio.sleep(0.2)
        running -= 1
        await original(self)

    monkeypatch.setattr(aioweb.AppRunner, "cleanup", slow_cleanup)
    task = asyncio.create_task(main.main())
    await asyncio.sleep(0.2)
    await _send_sigterm()
    assert await asyncio.wait_for(task, 5) is None
    assert max(overlap) == 2  # both servers stopped at the same time


async def test_run_timetable_wakes_on_change_and_cancels_jobs_on_stop(mgr, monkeypatch):
    """The timetable loop starts due jobs, is woken early by timetable_changed (a new
    schedule or plan) and cancels running jobs when it is cancelled (app stop)."""
    calls = []
    started = asyncio.Event()

    async def job():
        started.set()
        await asyncio.sleep(3600)

    def start_due(now):
        calls.append(now)
        if len(calls) == 2:
            mgr._spawn(job())

    monkeypatch.setattr(mgr, "start_due", start_due)
    monkeypatch.setattr(mgr, "due_in", lambda _now: float("inf"))  # nothing due: sleeps MAX_IDLE_WAIT_S
    loop = asyncio.create_task(main.run_timetable(mgr))
    await asyncio.sleep(0.01)
    assert len(calls) == 1
    mgr.timetable_changed.set()
    await asyncio.wait_for(started.wait(), 1)
    assert len(calls) == 2
    running = next(iter(mgr._timetable_tasks))
    loop.cancel()
    with pytest.raises(asyncio.CancelledError):
        await loop
    assert running.cancelled()
