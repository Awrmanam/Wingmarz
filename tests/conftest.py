import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

# The CI workflows install pytest without the optional pytest-asyncio plugin.
import asyncio
import inspect
import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "asyncio: execute an async test in its own event loop")


@pytest.hookimpl(tryfirst=True)
def pytest_pyfunc_call(pyfuncitem):
    if not pyfuncitem.get_closest_marker("asyncio"):
        return None
    func = pyfuncitem.obj
    if not inspect.iscoroutinefunction(func):
        return None
    kwargs = {name: pyfuncitem.funcargs[name] for name in inspect.signature(func).parameters}
    asyncio.run(func(**kwargs))
    return True
