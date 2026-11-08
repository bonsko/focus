import os
import tempfile
from pathlib import Path

import pytest

# config reads these at import time, so set them before importing the app
_tmp = Path(tempfile.mkdtemp(prefix="focus-tests-"))
os.environ["FOCUS_HOME"] = str(_tmp)
os.environ["FOCUS_TOKEN_PATH"] = str(_tmp / "hermes_token")

from fastapi.testclient import TestClient  # noqa: E402

from focus import config, db  # noqa: E402
from focus.main import app  # noqa: E402

TABLES = ("rambles", "jobs", "subtasks", "tasks", "projects", "users")


@pytest.fixture()
def client():
    db.init()
    con = db.connect()
    for table in TABLES:
        con.execute(f"DELETE FROM {table}")
    con.commit()
    con.close()
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def token():
    return config.TOKEN_PATH.read_text().strip()


def signup(client: TestClient, name: str = "nat", password: str = "hunter2") -> TestClient:
    res = client.post("/login", data={"username": name, "password": password})
    assert res.status_code == 200, res.text
    return client


@pytest.fixture()
def nat(client):
    return signup(client, "nat")
