import os

os.environ["DATABASE_URL"] = "sqlite://"
os.environ["JWT_SECRET"] = "test-secret-key-for-voltgrid-suite"
os.environ["SEED_ON_STARTUP"] = "false"
os.environ["ALLOW_METER_SIMULATION"] = "true"
os.environ["RUN_RECONCILER"] = "false"
os.environ["KEYVAULT_URL"] = ""
os.environ["AZURE_STORAGE_CONNECTION_STRING"] = ""
os.environ["MYSQL_SSL"] = "false"

import pytest
from fastapi.testclient import TestClient

import app.db as database
from app.db import init_db
from app.main import app
from app.models import Base
from app.seed import seed


@pytest.fixture()
def client():
    init_db(force=True)
    Base.metadata.drop_all(database.engine)
    Base.metadata.create_all(database.engine)
    db = database.SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    with TestClient(app) as test_client:
        yield test_client

