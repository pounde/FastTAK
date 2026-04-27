"""Tests for the fastak DB connection helper."""

from unittest.mock import patch

import pytest


def test_dsn_uses_explicit_url_when_set():
    from app.fastak_db import _build_dsn

    with patch("app.fastak_db.settings") as s:
        s.fastak_db_url = "postgresql://x:y@h/fastak"
        s.fastak_db_password = ""
        assert _build_dsn() == "postgresql://x:y@h/fastak"


def test_dsn_assembled_from_password_and_host():
    from app.fastak_db import _build_dsn

    with patch("app.fastak_db.settings") as s:
        s.fastak_db_url = ""
        s.fastak_db_password = "p@ss/word"
        s.app_db_host = "app-db"
        s.app_db_user = "fastak"
        assert _build_dsn() == "postgresql://fastak:p%40ss%2Fword@app-db:5432/fastak"


def test_dsn_raises_when_unconfigured():
    from app.fastak_db import _build_dsn

    with patch("app.fastak_db.settings") as s:
        s.fastak_db_url = ""
        s.fastak_db_password = ""
        with pytest.raises(ValueError):
            _build_dsn()
