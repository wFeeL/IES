from __future__ import annotations

import pytest

from ies_bot_skeleton.web.app import create_app
from ies_bot_skeleton.web.extensions import db
from ies_bot_skeleton.web.services.seed import ensure_seed_data


@pytest.fixture()
def app():
    app = create_app("testing")
    with app.app_context():
        db.create_all()
        ensure_seed_data(admin_password="admin123", analyst_password="analyst123")
        yield app
        db.session.remove()
        db.drop_all()


@pytest.fixture()
def client(app):
    return app.test_client()
