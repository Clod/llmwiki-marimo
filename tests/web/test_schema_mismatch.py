"""An index built with an older schema is reported in the browser, not as a traceback."""

from tests.web.conftest import SPANISH


def test_an_index_with_an_older_schema_shows_the_project_message_not_a_traceback(app, home):
    import sqlite3

    old = home / "vieja"
    (old / ".llmwiki").mkdir(parents=True)
    (old / "wiki").mkdir()
    (old / "sources").mkdir()
    legacy = sqlite3.connect(old / ".llmwiki" / "index.db")
    legacy.executescript("CREATE TABLE documents (id TEXT PRIMARY KEY, filename TEXT);")
    legacy.commit()
    legacy.close()

    from fastapi.testclient import TestClient

    client = TestClient(app, follow_redirects=False, cookies=SPANISH)  # an unhandled exception would raise here
    response = client.get("/w/vieja/pages/overview")
    assert response.status_code == 500
    assert "No se puede abrir el índice de esta wiki." in response.text
    assert "relative_path" in response.text and "Traceback" not in response.text
