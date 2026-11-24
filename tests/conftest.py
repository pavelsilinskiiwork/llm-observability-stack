import pytest
from fastapi.testclient import TestClient
from prometheus_client import CollectorRegistry

from llm_obs.app import create_app
from llm_obs.providers import Behavior, FakeProvider
from llm_obs.settings import Settings


@pytest.fixture
def registry():
    return CollectorRegistry()


@pytest.fixture
def provider():
    import random

    p = FakeProvider(latency_scale=0, rng=random.Random(1))
    p.behavior = Behavior(1.0, 0.0)  # no random failures: tests that want errors ask for them
    return p


@pytest.fixture
def make_client(registry, provider):
    def make(**settings):
        app = create_app(Settings(latency_scale=0, **settings), provider, registry)
        return TestClient(app)

    return make


@pytest.fixture
def client(make_client):
    return make_client()


def sample(registry, name, **labels):
    return registry.get_sample_value(name, labels) or 0.0


@pytest.fixture
def always_fail(provider):
    provider.behavior = Behavior(1.0, 1.0)
    return provider
