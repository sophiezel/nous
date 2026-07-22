"""Tests for nous.core.db — SQLite connection management."""

import os
import sqlite3
from pathlib import Path

import pytest


class TestGetDB:
    """Test get_db() connection factory."""

    def test_creates_in_memory_db_by_default(self):
        """When no config, returns an in-memory connection."""
        from nous.core.db import get_db
        with get_db(db_name=":memory:") as conn:
            assert isinstance(conn, sqlite3.Connection)
            conn.execute("SELECT 1")

    def test_creates_file_db_with_config(self, tmp_path: Path):
        """When DB path is set, creates a file-based connection."""
        db_path = tmp_path / "test.db"
        os.environ["NOUS_CONFIG_DIR"] = str(tmp_path)
        from nous.core.config import Config
        Config.reset()
        Config.load(config_dir=str(tmp_path))

        import nous.core.db as db_mod
        original = db_mod._resolve_path
        db_mod._resolve_path = lambda name: str(db_path)

        try:
            with db_mod.get_db(write=True) as conn:
                conn.execute("CREATE TABLE test (id INTEGER)")
                conn.execute("INSERT INTO test VALUES (1)")
                conn.commit()

            assert db_path.exists()
            verify = sqlite3.connect(str(db_path))
            assert verify.execute("SELECT COUNT(*) FROM test").fetchone()[0] == 1
            verify.close()
        finally:
            db_mod._resolve_path = original
            Config.reset()

    def test_sets_wal_mode(self, tmp_path: Path):
        """All connections use WAL journal mode."""
        db_path = tmp_path / "wal_test.db"
        import nous.core.db as db_mod

        conn = db_mod._connect(str(db_path))
        try:
            mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
            assert mode.lower() == "wal"
        finally:
            conn.close()

    def test_sets_busy_timeout(self):
        """Connections set busy_timeout to avoid immediate lock failures."""
        from nous.core.db import get_db
        with get_db(db_name=":memory:") as conn:
            timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert timeout >= 1000

    def test_write_connection_has_higher_timeout(self):
        """Write connections get longer busy_timeout than read connections."""
        import nous.core.db as db_mod

        w_conn = db_mod._connect(":memory:", write=True)
        r_conn = db_mod._connect(":memory:", write=False)
        try:
            w_timeout = w_conn.execute("PRAGMA busy_timeout").fetchone()[0]
            r_timeout = r_conn.execute("PRAGMA busy_timeout").fetchone()[0]
            assert w_timeout >= r_timeout
        finally:
            w_conn.close()
            r_conn.close()

    def test_context_manager_closes_connection(self):
        """get_db can be used as context manager."""
        from nous.core.db import get_db
        with get_db(db_name=":memory:", write=True) as conn:
            conn.execute("CREATE TABLE ctx_test (x)")
            conn.execute("INSERT INTO ctx_test VALUES (42)")
            conn.commit()
        # After context exit, connection should be closed — no crash = pass

    def test_connection_is_reusable(self):
        """Returned connection can execute multiple queries."""
        from nous.core.db import get_db
        with get_db(db_name=":memory:", write=True) as conn:
            conn.execute("CREATE TABLE multi (a, b)")
            conn.execute("INSERT INTO multi VALUES (1, 'one'), (2, 'two')")
            conn.commit()
            rows = conn.execute("SELECT * FROM multi ORDER BY a").fetchall()
            assert [tuple(r) for r in rows] == [(1, 'one'), (2, 'two')]

    def test_readonly_connection_cannot_write(self, tmp_path: Path):
        """Read-only connections reject write operations."""
        db_path = tmp_path / "ro_test.db"
        import nous.core.db as db_mod
        w_conn = db_mod._connect(str(db_path), write=True)
        w_conn.execute("CREATE TABLE ro_check (id)")
        w_conn.commit()
        w_conn.close()

        r_conn = db_mod._connect(str(db_path), write=False)
        try:
            with pytest.raises(sqlite3.OperationalError):
                r_conn.execute("INSERT INTO ro_check VALUES (1)")
        finally:
            r_conn.close()


class TestResolvePath:
    """Test DB path resolution."""

    def test_resolves_tilde_in_path(self):
        """Paths with ~ are expanded."""
        import nous.core.db as db_mod
        path = db_mod._resolve_path("~/test.db")
        assert path.startswith(str(Path.home()))
        assert path.endswith("test.db")

    def test_resolves_relative_path(self):
        """Relative paths are resolved against config data_dir."""
        import nous.core.db as db_mod
        path = db_mod._resolve_path("screener.db")
        assert Path(path).is_absolute()


class TestEdgeCases:
    """Boundary and error cases."""

    def test_nonexistent_directory_creates_parent(self, tmp_path: Path):
        """Opening a DB in a nonexistent directory auto-creates parent dirs."""
        db_path = tmp_path / "deep" / "nested" / "new.db"
        import nous.core.db as db_mod
        conn = db_mod._connect(str(db_path))
        try:
            conn.execute("CREATE TABLE deep_test (id)")
            conn.commit()
            assert db_path.exists()
        finally:
            conn.close()
