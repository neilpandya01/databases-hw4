import os
import pytest
from dotenv import load_dotenv
from sqlalchemy import delete, event, insert, inspect, select

load_dotenv()

from sakila_sync import cli  # noqa: E402
from sakila_sync.db import mysql_session, sqlite_session  # noqa: E402
from sakila_sync.models.sakila import (  # noqa: E402
    Actor, Address, Category, City, Country, Customer, Film, FilmActor,
    FilmCategory, Inventory, Language, Payment, Rental, Staff, Store
)

LOAD_ORDER = [Country, City, Address, Language, Film, Actor, Category, Store,
              Staff, Customer, FilmActor, FilmCategory, Inventory, Rental, Payment]

def _rows(session, model) -> list[dict]:
    keys = [a.key for a in inspect(model).column_attrs]
    return [{k: getattr(o, k) for k in keys} for o in session.scalars(select(model))]

@pytest.fixture(scope="session")
def snapshot():
    if os.environ["MYSQL_TEST_DATABASE"] == os.environ["MYSQL_DATABASE"]:
        pytest.exit("MYSQL_TEST_DATABASE must differ from MYSQL_DATABASE", returncode=2)
    src = mysql_session()
    try:
        return {m: _rows(src, m) for m in LOAD_ORDER}
    finally:
        src.close()

@pytest.fixture
def sakila(snapshot):
    session = mysql_session(test=True)
    engine = session.get_bind()
    event.listen(engine, "connect", lambda conn, _: conn.cursor().execute("SET FOREIGN_KEY_CHECKS=0"))
    for model in reversed(LOAD_ORDER):
        session.execute(delete(model))
    for model in LOAD_ORDER:
        if snapshot[model]:
            session.execute(insert(model), snapshot[model])
    session.commit()
    yield session
    session.close()

@pytest.fixture
def analytics(tmp_path, monkeypatch):
    monkeypatch.setenv("SQLITE_TEST_PATH", str(tmp_path / "analytics_test.db"))
    sessions = []

    def open_session():
        s = sqlite_session(test=True)
        sessions.append(s)
        return s

    yield open_session
    for s in sessions:
        s.close()

@pytest.fixture
def run(sakila, analytics, capsys):
    def _run(*args):
        code = cli.main(["--test", *args])
        out = capsys.readouterr()
        return code, out.out, out.err
    return _run

@pytest.fixture
def loaded(run):
    """Test databases after `init` and `full-load`, the starting point for later commands."""
    run("init")
    run("full-load")
