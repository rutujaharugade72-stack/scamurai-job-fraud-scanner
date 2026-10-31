"""Test setup: isolated temp database, no real .env, rate limits off by default."""
import os
import sys
import tempfile
import warnings

import pytest

warnings.filterwarnings("ignore")

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "src"))

_tmp = tempfile.mkdtemp(prefix="sentinel-test-")
os.environ["DATABASE_URL"] = "sqlite:///" + os.path.join(_tmp, "test.db").replace("\\", "/")
os.environ["SENTINEL_ENV"] = "testing"
os.environ["SENTINEL_SECRET_KEY"] = "test-secret-key"


@pytest.fixture(scope="session")
def flask_app():
    import app as sentinel_app
    sentinel_app.app.config["TESTING"] = True
    return sentinel_app


@pytest.fixture()
def client(flask_app):
    return flask_app.app.test_client()


def _register(client, username, password="pass1234"):
    return client.post("/register", data={"username": username, "password": password, "confirm": password})


@pytest.fixture()
def auth_client(flask_app):
    """A logged-in client with its own throw-away user."""
    import uuid
    c = flask_app.app.test_client()
    _register(c, "user_" + uuid.uuid4().hex[:8])
    return c


@pytest.fixture()
def two_users(flask_app):
    import uuid
    a, b = flask_app.app.test_client(), flask_app.app.test_client()
    _register(a, "alice_" + uuid.uuid4().hex[:6])
    _register(b, "bob_" + uuid.uuid4().hex[:6])
    return a, b
