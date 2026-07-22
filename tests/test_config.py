"""Tests for nous.core.config"""

import os
from pathlib import Path

import pytest
import yaml


@pytest.fixture(autouse=True)
def _reset_config():
    """Reset config singleton before each test to avoid cross-test pollution."""
    from nous.core.config import Config
    Config.reset()
    yield
    Config.reset()


class TestConfigLoad:
    """Test basic config loading."""

    def test_loads_default_yaml(self, tmp_path: Path):
        """Config loads from default.yaml and provides dot-access."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        default = config_dir / "default.yaml"
        default.write_text(yaml.dump({"nous": {"env": "development"}, "database": {"path": "test.db"}}))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        from nous.core.config import Config

        cfg = Config.load(force_reload=True)
        assert cfg.nous.env == "development"
        assert cfg.database.path == "test.db"

    def test_env_override_merges(self, tmp_path: Path):
        """Production config overrides default values."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(yaml.dump({
            "nous": {"env": "development", "debug": True},
            "api": {"port": 8000},
        }))
        (config_dir / "production.yaml").write_text(yaml.dump({
            "nous": {"env": "production", "debug": False},
            "api": {"host": "0.0.0.0"},
        }))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        os.environ["NOUS_ENV"] = "production"
        from nous.core.config import Config

        cfg = Config.load(force_reload=True)
        assert cfg.nous.env == "production"
        assert cfg.nous.debug is False
        assert cfg.api.port == 8000  # not overridden
        assert cfg.api.host == "0.0.0.0"  # overridden

    def test_env_var_override(self, tmp_path: Path):
        """Environment variables with NOUS_ prefix override config."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(yaml.dump({
            "database": {"path": "default.db"},
        }))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        os.environ["NOUS_DATABASE__PATH"] = "env_override.db"
        from nous.core.config import Config

        cfg = Config.load(force_reload=True)
        assert cfg.database.path == "env_override.db"

    def test_missing_default_falls_back_to_package(self):
        """When no config dir is set, falls back to built-in defaults."""
        os.environ.pop("NOUS_CONFIG_DIR", None)
        os.environ.pop("NOUS_ENV", None)
        from nous.core.config import Config

        cfg = Config.load()
        assert cfg.nous.env == "development"
        assert cfg.api.port == 8000

    def test_nonexistent_config_dir_uses_defaults(self):
        """Nonexistent config dir uses built-in defaults without crashing."""
        os.environ["NOUS_CONFIG_DIR"] = "/nonexistent/path"
        from nous.core.config import Config

        cfg = Config.load()
        assert cfg.nous.env == "development"


class TestConfigEdgeCases:
    """Edge case handling."""

    def test_empty_yaml_uses_defaults(self, tmp_path: Path):
        """Empty YAML file doesn't crash — fills in defaults."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text("")

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        from nous.core.config import Config

        cfg = Config.load()
        assert cfg.nous.env == "development"

    def test_partial_yaml_merges_defaults(self, tmp_path: Path):
        """Partial YAML fills missing keys from defaults."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(yaml.dump({
            "api": {"port": 9999}
        }))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        from nous.core.config import Config

        cfg = Config.load()
        assert cfg.api.port == 9999
        # Default values still present for other sections
        assert cfg.database.screener.busy_timeout == 30000

    def test_repr_masks_secret_values(self, tmp_path: Path):
        """Config repr masks secret values with ***."""
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(yaml.dump({
            "llm": {"api_key_env": "DEEPSEEK_API_KEY", "model": "test-model"},
        }))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        from nous.core.config import Config

        cfg = Config.load()
        r = repr(cfg.llm)
        # The value should be masked, key is still visible
        assert "***" in r
        assert "DEEPSEEK_API_KEY" not in r  # actual secret value hidden
        assert "test-model" in r  # non-secret visible


class TestConfigSingleton:
    """Config is a singleton — second load returns same instance."""

    def test_load_returns_same_instance(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(yaml.dump({"nous": {"env": "test"}}))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        from nous.core.config import Config

        Config.reset()
        cfg1 = Config.load()
        cfg2 = Config.load()  # no force_reload — should return cached
        assert cfg1 is cfg2


class TestConfigReset:
    """Reset clears singleton for testing."""

    def test_force_reload_returns_new_instance(self, tmp_path: Path):
        config_dir = tmp_path / "config"
        config_dir.mkdir()
        (config_dir / "default.yaml").write_text(yaml.dump({"nous": {"env": "first"}}))

        os.environ["NOUS_CONFIG_DIR"] = str(config_dir)
        from nous.core.config import Config

        cfg1 = Config.load()
        # Change the file
        (config_dir / "default.yaml").write_text(yaml.dump({"nous": {"env": "second"}}))
        cfg2 = Config.load(force_reload=True)
        assert cfg1.nous.env == "first"
        assert cfg2.nous.env == "second"
        assert cfg1 is not cfg2
