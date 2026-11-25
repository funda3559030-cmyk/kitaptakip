import pytest

import application


@pytest.fixture
def client(tmp_path, monkeypatch):
    application.app.config.update(TESTING=True)
    application.db.DB_PATH = tmp_path / "kitap.db"
    application.db.init_db(application.db.DB_PATH)
    with application.app.test_client() as test_client:
        yield test_client
