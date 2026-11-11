"""Minimal stand-in for pytest-asyncio's "auto" mode (workspace helper, not shipped).

Used only where pytest-asyncio is not installed and cannot be installed (offline
workspace). Runs every async test and async fixture on one fresh event loop per
test, as pyproject.toml asks for (asyncio_mode = auto, function-scoped loop).
Loaded with -p asyncio_shim (tools/dev/run.sh puts tools/dev/ on PYTHONPATH)."""

import asyncio
import functools
import inspect

import pytest

_loop = None


def pytest_addoption(parser):
    """Accept the pytest-asyncio ini keys."""
    parser.addini("asyncio_mode", "ignored")
    parser.addini("asyncio_default_fixture_loop_scope", "ignored")


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    """One new event loop per test."""
    global _loop
    _loop = asyncio.new_event_loop()
    asyncio.set_event_loop(_loop)
    try:
        yield
    finally:
        pending = [t for t in asyncio.all_tasks(_loop) if not t.done()]
        for task in pending:
            task.cancel()
        if pending:
            _loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
        _loop.run_until_complete(_loop.shutdown_asyncgens())
        _loop.close()
        asyncio.set_event_loop(None)
        _loop = None


@pytest.hookimpl(tryfirst=True)
def pytest_fixture_setup(fixturedef, request):
    """Wrap async fixtures."""
    func = fixturedef.func
    if getattr(func, "_shim_wrapped", False):
        return
    if inspect.iscoroutinefunction(func):

        @functools.wraps(func)
        def sync(*args, **kwargs):
            return _loop.run_until_complete(func(*args, **kwargs))

    elif inspect.isasyncgenfunction(func):

        @functools.wraps(func)
        def sync(*args, **kwargs):
            gen = func(*args, **kwargs)
            yield _loop.run_until_complete(gen.__anext__())
            try:
                _loop.run_until_complete(gen.__anext__())
            except StopAsyncIteration:
                pass

    else:
        return
    sync._shim_wrapped = True
    fixturedef.func = sync


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    """Run an async test on the test's loop."""
    if not inspect.iscoroutinefunction(pyfuncitem.obj):
        return None
    args = {n: pyfuncitem.funcargs[n] for n in pyfuncitem._fixtureinfo.argnames}
    _loop.run_until_complete(pyfuncitem.obj(**args))
    return True
