"""
Fixtures for unit tests of the nm-gpclient D-Bus service.

The service imports the `sdbus` module at import time, which is not
available (and not needed) for unit-testing the pure parsing helpers.
A minimal stub is injected before the service module is loaded.
"""

import importlib.util
import os
import sys
import types

import pytest

SERVICE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "..", "service", "nm-gpclient-service.py"
)


def _install_sdbus_stub():
    if "sdbus" in sys.modules:
        return

    stub = types.ModuleType("sdbus")

    class DbusInterfaceCommonAsync:
        def __init_subclass__(cls, **kwargs):
            pass

    # D-Bus signals are emitted as `self.SignalName.emit(payload)`. Give the
    # decorated functions an `emit` attribute (bound methods expose the
    # function's attributes) that records the call, so tests can assert what the
    # service reported to NetworkManager.
    signal_calls = []

    def _decorator_factory(*_args, **_kwargs):
        def decorator(func):
            func.emit = lambda *payload: signal_calls.append(
                (func.__name__, payload[0] if len(payload) == 1 else payload)
            )
            return func

        return decorator

    stub.DbusInterfaceCommonAsync = DbusInterfaceCommonAsync
    stub.dbus_method_async = _decorator_factory
    stub.dbus_property_async = _decorator_factory
    stub.dbus_signal_async = _decorator_factory

    async def _noop_async(*_args, **_kwargs):
        return None

    stub.request_default_bus_name_async = _noop_async
    stub.sd_bus_open_system = lambda: None
    stub.set_default_bus = lambda bus: None
    stub.SIGNAL_CALLS = signal_calls

    sys.modules["sdbus"] = stub


@pytest.fixture(scope="session")
def service_module():
    """Import service/nm-gpclient-service.py as a module (with sdbus stubbed)."""
    _install_sdbus_stub()
    spec = importlib.util.spec_from_file_location(
        "nm_gpclient_service", os.path.abspath(SERVICE_PATH)
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dbus_signals():
    """D-Bus signals the service emitted, as (name, payload) - cleared per test"""
    _install_sdbus_stub()
    calls = sys.modules["sdbus"].SIGNAL_CALLS
    calls.clear()
    yield calls
    calls.clear()
