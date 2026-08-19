from pathlib import Path

from nous.core import volume


def test_archive_job_runs_while_dirs_are_local(tmp_path, monkeypatch):
    cfg_path = tmp_path / "storage.toml"
    work = tmp_path / "nous-data"
    (work / "backups").mkdir(parents=True)
    (work / "factors").mkdir()
    cfg_path.write_text(
        "\n".join(
            [
                f'working_dir = "{work}"',
                "[volumes.archive]",
                'role = "archive"',
                'uuid = "00000000-0000-0000-0000-000000000000"',
                'prefix = "nous-data"',
                "[archive]",
                'dirs = ["backups", "factors"]',
                "[jobs]",
                'require_archive = ["db-backup", "daily-update"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NOUS_STORAGE_CONFIG", str(cfg_path))
    monkeypatch.setattr(volume, "archive_ready", lambda cfg=None: False)

    cfg = volume.load_config()
    assert volume.archive_dirs_attached(cfg) is False
    assert volume.archive_job_should_skip("db-backup", cfg) is False
    assert volume.archive_job_should_skip("daily-update", cfg) is False


def test_archive_job_skips_when_linked_and_volume_missing(tmp_path, monkeypatch):
    cfg_path = tmp_path / "storage.toml"
    work = tmp_path / "nous-data"
    work.mkdir()
    target = tmp_path / "archive" / "backups"
    target.mkdir(parents=True)
    (work / "backups").symlink_to(target)
    (work / "factors").symlink_to(tmp_path / "archive" / "factors")
    (tmp_path / "archive" / "factors").mkdir()
    cfg_path.write_text(
        "\n".join(
            [
                f'working_dir = "{work}"',
                "[volumes.archive]",
                'role = "archive"',
                'uuid = "00000000-0000-0000-0000-000000000000"',
                "[archive]",
                'dirs = ["backups", "factors"]',
                "[jobs]",
                'require_archive = ["db-backup"]',
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("NOUS_STORAGE_CONFIG", str(cfg_path))
    monkeypatch.setattr(volume, "archive_ready", lambda cfg=None: False)

    cfg = volume.load_config()
    assert volume.archive_dirs_attached(cfg) is True
    assert volume.archive_job_should_skip("db-backup", cfg) is True
    assert volume.archive_job_should_skip("minute-collector", cfg) is False


def test_check_cli_returns_skip_exit(tmp_path, monkeypatch):
    work = tmp_path / "nous-data"
    work.mkdir()
    (work / "backups").symlink_to(tmp_path / "missing-backups")
    (work / "factors").symlink_to(tmp_path / "missing-factors")
    cfg_path = tmp_path / "storage.toml"
    cfg_path.write_text(
        f'working_dir = "{work}"\n'
        "[volumes.archive]\n"
        'uuid = "00000000-0000-0000-0000-000000000000"\n'
        "[archive]\n"
        'dirs = ["backups", "factors"]\n'
        "[jobs]\n"
        'require_archive = ["db-backup"]\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("NOUS_STORAGE_CONFIG", str(cfg_path))
    monkeypatch.setattr(volume, "archive_ready", lambda cfg=None: False)
    assert volume.main(["check", "db-backup"]) == volume.SKIP_EXIT
    assert volume.main(["check", "daily-update"]) == 0
