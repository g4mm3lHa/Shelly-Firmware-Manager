"""rc4: an aborted check leaves no shared offer/download task running.

Regression guard: the shared single-flight tasks are awaited by the device tasks,
so cancelling the check cancels them as well (their .part file is removed by
shelly._store)."""

import asyncio

import pytest
import manager
from conftest import A


def _foreign_pending():
    """Unfinished tasks other than the running test itself."""
    current = asyncio.current_task()
    return [t for t in asyncio.all_tasks() if t is not current and not t.done()]


async def test_cancelled_check_leaves_no_running_download(mgr, net):
    """App stop during a download: the shared download task is cancelled too."""
    net.download_gate = asyncio.Event()  # the download never finishes on its own
    task = asyncio.create_task(mgr.scan())
    await asyncio.sleep(0.05)
    assert mgr._provisions  # a shared download task is running
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr._provisions == {}
    assert _foreign_pending() == []


async def test_cancelled_single_check_leaves_no_running_download(mgr, net):
    """Same for the single-device check."""
    await mgr.scan()  # device known
    for name in list(mgr.firmware):  # force a new download (rc7: a provided file
        (manager.FW_DIR / name).unlink()  # cannot be deleted in the UI, remove it directly)
    mgr.firmware.clear()
    net.download_gate = asyncio.Event()
    mgr.reserve_check("device", A)
    task = asyncio.create_task(mgr.check_device(A))
    await asyncio.sleep(0.05)
    assert mgr._provisions
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert mgr._provisions == {}
    assert _foreign_pending() == []

