"""Entry point: load options, start both web servers, run the check schedule.

SIGTERM (stop by the Supervisor) and SIGINT shut the app down cleanly with exit
code 0; see main()."""

import asyncio
import contextlib
import json
import logging
import os
import signal
import time
from pathlib import Path
from typing import Any

import aiohttp
import schedule
from aiohttp import web
from download_server import build_download_app
from manager import Manager
from tls import CertificatePins
from web_ui import build_ui_app

VERSION = "3.6.0"  # must match config.yaml

OPTIONS_FILE = Path("/data/options.json")
UI_PORT = 8099  # Ingress (internal)
DOWNLOAD_PORT = 8098  # Shelly downloads (published via "ports")
STARTUP_DELAY_S = 30  # let Home Assistant settle before the first check
# Graceful stop: the Supervisor sends SIGTERM and kills the app after its stop timeout
# (default 10 s). Open UI connections get at most this long to finish, so the whole
# shutdown (cancelling checks/installations, saving the state) stays well inside it.
SHUTDOWN_TIMEOUT_S = 5
# Longest single wait of the scheduler: protects against wall-clock jumps (the
# wait itself uses the monotonic clock) while keeping idle wake-ups rare.
MAX_IDLE_WAIT_S = 3600

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("sfm")


# Neutral User-Agent: tells the Shelly servers neither app name nor version
USER_AGENT = "Mozilla/5.0"


async def start_site(app: web.Application, port: int) -> web.AppRunner:
    """Start an aiohttp application on all interfaces of the container."""
    # access_log=None: own, concise logging instead of an access log
    runner = web.AppRunner(app, access_log=None, shutdown_timeout=SHUTDOWN_TIMEOUT_S)
    await runner.setup()
    # Binding to all interfaces is required in the container; access is
    # controlled by the middlewares (Ingress IP or file/device checks).
    await web.TCPSite(runner, "0.0.0.0", port).start()  # nosec B104  # noqa: S104
    return runner


def load_options() -> dict[str, Any]:
    """Options written by the Supervisor (validated against config.yaml)."""
    options = json.loads(OPTIONS_FILE.read_text())
    if not isinstance(options, dict):
        raise SystemExit(f"{OPTIONS_FILE} does not contain a JSON object")
    return options


async def main() -> None:
    """Load the options, start both servers and run the check schedule."""
    token = os.environ.get("SUPERVISOR_TOKEN")
    if not token:
        raise SystemExit("SUPERVISOR_TOKEN missing – homeassistant_api must be enabled")
    options = load_options()

    # Certificate pins for Shelly servers with an in-house CA (see tls.py)
    try:
        pins = CertificatePins(options.get("pinned_certificates", []))
    except ValueError as err:
        raise SystemExit(f"Configuration error: {err}") from None

    async with aiohttp.ClientSession(headers={"User-Agent": USER_AGENT}, trust_env=False) as session:
        mgr = Manager(options, session, token, pins)
        runners = [
            await start_site(build_ui_app(mgr, app_version=VERSION), UI_PORT),
            await start_site(build_download_app(mgr), DOWNLOAD_PORT),
        ]
        log.info(
            "Firmware Manager for Shelly %s ready – check every %s day(s) at %s (%s), next %s, downloads at %s",
            VERSION,
            mgr.check_days,
            mgr.check_time,
            mgr.tz_name,
            schedule.iso_utc(schedule.next_run(mgr.schedule, time.time(), mgr.tz)),
            mgr.download_base or "(not set – installations disabled)",
        )
        if mgr.download_base_issue == "unconfirmed":
            log.warning("download_base_url is the former example value – confirm it in the Settings tab")
        for host, pin in sorted(pins.pins.items()):
            log.info("Certificate pinning active for %s (%d fingerprint(s))", host, len(pin.digests))
        # SIGTERM (Supervisor stop) and SIGINT end the app cleanly with exit code 0:
        # the scheduler is cancelled, then both servers are cleaned up, which also
        # cancels running checks and installations (web_ui.cancel_background); their
        # finally blocks save the state, so an interrupted installation is reported as
        # "Unconfirmed" after the next start, never as success.
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(sig, stop.set)
        await mgr.announce_first_start()   # fresh installation: one HA notification about the import question
        scheduler = asyncio.create_task(run_schedule(mgr))
        stopper = asyncio.create_task(stop.wait())
        results: tuple[object, ...] = (None, None)
        try:
            await asyncio.wait({scheduler, stopper}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            log.info("Stopping – cancelling running work and saving the state")
            for task in (scheduler, stopper):
                task.cancel()
            results = await asyncio.gather(scheduler, stopper, return_exceptions=True)
            # Both servers in parallel: the stop needs at most one
            # SHUTDOWN_TIMEOUT_S, not one per server – well inside the 10 s of the Supervisor
            await asyncio.gather(*(runner.cleanup() for runner in runners))
            log.info("Stopped")
        # A scheduler that ended with an error is reported (non-zero exit code)
        if isinstance(results[0], Exception):
            raise results[0]


async def run_schedule(mgr: Manager) -> None:
    """Run the automatic check schedule until cancelled."""
    await asyncio.sleep(STARTUP_DELAY_S)
    while True:
        # Sleep until the next scheduled date/time (schedule.py), at most
        # MAX_IDLE_WAIT_S at a time so DST and wall-clock jumps are picked
        # up; schedule_changed (set after every check) wakes it early.
        mgr.schedule_changed.clear()
        if mgr.first_start_open:   # fresh installation: no check before the import question is answered
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(mgr.schedule_changed.wait(), timeout=MAX_IDLE_WAIT_S)
            continue
        due = mgr.scan_due_in(time.time())
        if due > 0:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(mgr.schedule_changed.wait(), timeout=min(due, MAX_IDLE_WAIT_S))
            continue
        # The attempt is recorded when it is due; a check that cannot start
        # (other work running) is cancelled and logged, not repeated
        await mgr.scan(automatic=True)


if __name__ == "__main__":
    asyncio.run(main())
