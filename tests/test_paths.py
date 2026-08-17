from pathlib import Path


def test_screener_db_uses_nous_data(monkeypatch, tmp_path):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    from nous.core.config import Config

    Config.reset()
    from nous.core.paths import factor_dir, screener_db

    assert screener_db() == tmp_path / "screener.db"
    assert factor_dir() == tmp_path / "factors"


def test_coarse_filter_default_is_nous_data(monkeypatch, tmp_path):
    monkeypatch.setenv("NOUS_DATA_DIR", str(tmp_path))
    from nous.core.config import Config

    Config.reset()
    from nous.engine.pipelines.coarse_filter import _get_default_db

    assert Path(_get_default_db()) == tmp_path / "screener.db"
