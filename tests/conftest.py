import os

import pytest


def pytest_configure(config):
    config.addinivalue_line("markers", "docker: needs Docker; runs only with RUN_DOCKER=1")


def pytest_collection_modifyitems(config, items):
    if os.getenv("RUN_DOCKER") == "1":
        return
    skip = pytest.mark.skip(reason="Docker test: set RUN_DOCKER=1 to run")
    for item in items:
        if "docker" in item.keywords:
            item.add_marker(skip)
