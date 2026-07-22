"""Unit tests for the live API transport config (spec §8.4)."""

import pytest

from syncee_scanner.browser.transport import SynceeApiTransport, _origin
from syncee_scanner.config import load_config
from syncee_scanner.extraction.mapper import SynceeMapping
from syncee_scanner.observability.errors import ConfigurationError


def cfg():
    return load_config()


def test_requires_endpoint_template():
    with pytest.raises(ConfigurationError):
        SynceeApiTransport(cfg(), SynceeMapping())  # no endpoint_template set


def test_reads_endpoint_and_method():
    mapping = SynceeMapping()
    mapping.list.endpoint_template = "https://gw.syncee.test/products/search"
    mapping.list.method = "POST"
    t = SynceeApiTransport(cfg(), mapping)
    assert t.endpoint.endswith("/products/search")
    assert t.method == "POST"
    assert t.origin == "https://syncee.com"


def test_origin_helper():
    assert _origin("https://syncee.com") == "https://syncee.com"
    assert _origin("https://app.example.com/path") == "https://app.example.com"
