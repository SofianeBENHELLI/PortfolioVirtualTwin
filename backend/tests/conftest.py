import os
import tempfile

# Must run before any app import: point the app at a throwaway SQLite DB.
_tmpdir = tempfile.mkdtemp(prefix="pvt-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmpdir}/test.db"
os.environ["OPENAI_API_KEY"] = ""
os.environ["ALPACA_API_KEY"] = ""

import pytest  # noqa: E402


@pytest.fixture()
def db():
    import app.models  # noqa: F401
    from app.core.db import Base, SessionLocal, engine

    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    session = SessionLocal()
    yield session
    session.close()
