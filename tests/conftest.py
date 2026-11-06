"""Shared pytest wiring for the acceptance suite.

`slow` marks the checks that drive a whole episode (or the HTTP stack) instead of one function:
they are still offline and still must pass, they just cost seconds rather than milliseconds, so
`pytest -m "not slow"` is the fast loop and `pytest tests -q` is the promise.
"""


def pytest_configure(config) -> None:
    config.addinivalue_line("markers", "slow: drives a full episode or the HTTP stack; still offline")
