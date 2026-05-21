import pytest
from app import create_app
from app.extensions import db as _db


@pytest.fixture(scope="session")
def app():
    """Session-scoped Flask app with an in-memory SQLite database."""
    application = create_app("testing")
    with application.app_context():
        _db.create_all()
        yield application
        _db.drop_all()


@pytest.fixture
def app_ctx(app):
    """Push/pop an app context for tests that need it."""
    with app.app_context():
        yield app


@pytest.fixture
def client(app):
    return app.test_client()
