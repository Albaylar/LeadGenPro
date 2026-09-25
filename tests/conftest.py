import pytest
import db as db_module


@pytest.fixture
def test_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test.db")
    monkeypatch.setattr(db_module, "DB", db_path)
    db_module.init_db()
    yield db_path
