"""Removable-volume roles for nous data.

Working directory stays on internal APFS. Archive/disaster volumes are
identified by UUID (never by a hardcoded mount path). Archive jobs skip
when those dirs have been attached as symlinks and the volume is gone.

This module is stdlib-only so the scheduler can invoke it as:
    python3 -m nous.core.volume check <job>
or via ~/bin/nous-storage.
"""
from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

try:
    import tomllib
except ModuleNotFoundError:  # pragma: no cover
    tomllib = None  # type: ignore


def _unquote(value: str) -> str:
    value = value.strip().rstrip(",")
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


def _parse_simple_toml(text: str) -> dict[str, Any]:
    """Minimal TOML reader for this config (Python 3.9 fallback)."""
    data: dict[str, Any] = {}
    cursor: dict[str, Any] = data
    pending_key: str | None = None
    pending_items: list[str] | None = None

    def nest(keys: list[str]) -> dict[str, Any]:
        node: dict[str, Any] = data
        for key in keys:
            node = node.setdefault(key, {})
        return node

    for raw in text.splitlines():
        stripped = raw.strip()
        if pending_items is not None:
            if "]" in stripped:
                piece = stripped.split("]", 1)[0].strip().rstrip(",")
                if piece:
                    pending_items.append(_unquote(piece))
                assert pending_key is not None
                cursor[pending_key] = pending_items
                pending_key = None
                pending_items = None
            else:
                piece = stripped.rstrip(",")
                if piece:
                    pending_items.append(_unquote(piece))
            continue
        line = stripped.split("#", 1)[0].strip()
        if not line:
            continue
        if line.startswith("[") and line.endswith("]"):
            cursor = nest(line[1:-1].split("."))
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        if val.startswith("[") and not val.endswith("]"):
            pending_key = key
            pending_items = []
            inner = val[1:].strip().rstrip(",")
            if inner:
                pending_items.append(_unquote(inner))
            continue
        if val.startswith("[") and val.endswith("]"):
            inner = val[1:-1].strip()
            cursor[key] = [_unquote(p.strip()) for p in inner.split(",") if p.strip()]
            continue
        if val in {"true", "false"}:
            cursor[key] = val == "true"
        else:
            cursor[key] = _unquote(val)
    return data


SKIP_EXIT = 75  # EX_TEMPFAIL: job skipped, not a failure
DEFAULT_CONFIG = Path.home() / ".config" / "nous" / "storage.toml"
DEFAULT_ARCHIVE_JOBS = (
    "db-backup",
    "weekly-train",
    "factor-full-recompute",
    "monthly-archive",
    "factor-freshness",
    "daily-recommend",
)
DEFAULT_ARCHIVE_DIRS = ("backups", "factors")


def config_path() -> Path:
    override = os.environ.get("NOUS_STORAGE_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CONFIG


def load_config(path: Path | None = None) -> dict[str, Any]:
    p = path or config_path()
    if not p.is_file():
        return {
            "working_dir": str(Path.home() / "nous-data"),
            "volumes": {},
            "archive": {"dirs": list(DEFAULT_ARCHIVE_DIRS)},
            "jobs": {"require_archive": list(DEFAULT_ARCHIVE_JOBS)},
        }
    raw = p.read_text(encoding="utf-8")
    if tomllib is not None:
        data = tomllib.loads(raw)
    else:
        data = _parse_simple_toml(raw)
    data.setdefault("working_dir", str(Path.home() / "nous-data"))
    data.setdefault("volumes", {})
    data.setdefault("archive", {})
    data["archive"].setdefault("dirs", list(DEFAULT_ARCHIVE_DIRS))
    data.setdefault("jobs", {})
    data["jobs"].setdefault("require_archive", list(DEFAULT_ARCHIVE_JOBS))
    return data


def working_dir(cfg: dict[str, Any] | None = None) -> Path:
    cfg = cfg if cfg is not None else load_config()
    return Path(str(cfg.get("working_dir", "~/nous-data"))).expanduser()


def archive_dirs(cfg: dict[str, Any] | None = None) -> tuple[str, ...]:
    cfg = cfg if cfg is not None else load_config()
    dirs = cfg.get("archive", {}).get("dirs") or list(DEFAULT_ARCHIVE_DIRS)
    return tuple(str(d) for d in dirs)


def require_archive_jobs(cfg: dict[str, Any] | None = None) -> frozenset[str]:
    cfg = cfg if cfg is not None else load_config()
    jobs = cfg.get("jobs", {}).get("require_archive") or list(DEFAULT_ARCHIVE_JOBS)
    return frozenset(str(j) for j in jobs)


def _volume_block(cfg: dict[str, Any], role: str) -> dict[str, Any] | None:
    volumes = cfg.get("volumes") or {}
    block = volumes.get(role)
    if isinstance(block, dict) and block.get("uuid"):
        return block
    for item in volumes.values():
        if isinstance(item, dict) and item.get("role") == role and item.get("uuid"):
            return item
    return None


def diskutil_info(uuid: str) -> dict[str, Any]:
    result = subprocess.run(
        ["diskutil", "info", "-plist", uuid],
        capture_output=True,
        check=False,
    )
    if result.returncode != 0 or not result.stdout:
        return {}
    try:
        return plistlib.loads(result.stdout)
    except Exception:
        return {}


def volume_status(role: str, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    cfg = cfg if cfg is not None else load_config()
    block = _volume_block(cfg, role)
    if not block:
        return {
            "role": role,
            "configured": False,
            "mounted": False,
            "uuid": "",
            "name": "",
            "mount_point": None,
            "root": None,
        }
    uuid = str(block["uuid"])
    info = diskutil_info(uuid)
    mount = info.get("MountPoint") or ""
    mounted = bool(mount) and Path(mount).is_dir()
    prefix = str(block.get("prefix") or "nous-data").lstrip("/")
    root = str(Path(mount) / prefix) if mounted else None
    return {
        "role": role,
        "configured": True,
        "mounted": mounted,
        "uuid": uuid,
        "name": str(block.get("name") or info.get("VolumeName") or role),
        "mount_point": mount or None,
        "root": root,
        "filesystem": info.get("FilesystemName") or info.get("FilesystemPersonality") or "",
    }


def archive_ready(cfg: dict[str, Any] | None = None) -> bool:
    return bool(volume_status("archive", cfg)["mounted"])


def archive_dirs_attached(cfg: dict[str, Any] | None = None) -> bool:
    """True after migrate: working copies of archive dirs are symlinks."""
    cfg = cfg if cfg is not None else load_config()
    base = working_dir(cfg)
    names = archive_dirs(cfg)
    if not names:
        return False
    return all((base / name).is_symlink() for name in names)


def archive_job_should_skip(job_name: str, cfg: dict[str, Any] | None = None) -> bool:
    cfg = cfg if cfg is not None else load_config()
    if job_name not in require_archive_jobs(cfg):
        return False
    if not archive_dirs_attached(cfg):
        return False
    return not archive_ready(cfg)


def _rsync(src: Path, dest: Path) -> None:
    dest.mkdir(parents=True, exist_ok=True)
    cmd = [
        "rsync",
        "-aH",
        "--progress",
        "--stats",
        f"{src}/",
        f"{dest}/",
    ]
    print(f"rsync {src} -> {dest}", flush=True)
    subprocess.run(cmd, check=True)


def _dir_stats(path: Path) -> tuple[int, int]:
    """Return (file_count, total_bytes) for a tree, following the path itself."""
    if not path.exists():
        return 0, 0
    files = 0
    total = 0
    for root, _dirs, filenames in os.walk(path, followlinks=True):
        for name in filenames:
            fp = Path(root) / name
            try:
                total += fp.stat().st_size
                files += 1
            except OSError:
                pass
    return files, total


def migrate_archive(*, yes: bool = False, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg if cfg is not None else load_config()
    st = volume_status("archive", cfg)
    if not st["mounted"] or not st["root"]:
        print("archive volume is not mounted / unlocked", file=sys.stderr)
        return 1
    if not yes:
        print("refusing to migrate without --yes", file=sys.stderr)
        return 2

    base = working_dir(cfg)
    archive_root = Path(st["root"])
    archive_root.mkdir(parents=True, exist_ok=True)
    names = archive_dirs(cfg)

    for name in names:
        src = base / name
        dest = archive_root / name
        if src.is_symlink():
            print(f"skip {name}: already a symlink -> {src.readlink()}")
            continue
        if not src.exists():
            print(f"skip {name}: missing locally, creating empty archive dir")
            dest.mkdir(parents=True, exist_ok=True)
            src.symlink_to(dest)
            continue
        _rsync(src, dest)
        src_files, src_bytes = _dir_stats(src)
        dest_files, dest_bytes = _dir_stats(dest)
        print(f"verify {name}: local {src_files} files/{src_bytes}B  archive {dest_files} files/{dest_bytes}B")
        if src_files != dest_files:
            print(f"file count mismatch for {name}", file=sys.stderr)
            return 1
        local_hold = base / f".{name}.pre-migrate"
        if local_hold.exists():
            shutil.rmtree(local_hold)
        src.rename(local_hold)
        src.symlink_to(dest)
        if not src.is_dir():
            print(f"symlink for {name} is not readable; restoring local dir", file=sys.stderr)
            src.unlink(missing_ok=True)
            local_hold.rename(src)
            return 1
        print(f"attached {name}: {src} -> {dest}")
        shutil.rmtree(local_hold)
        print(f"deleted local copy {local_hold}")
    return 0


def safe_eject(*, yes: bool = False, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg if cfg is not None else load_config()
    st = volume_status("archive", cfg)
    if not st["mounted"] or not st["mount_point"]:
        print("archive volume already unmounted")
        return 0
    if not yes:
        print("refusing to eject without --yes", file=sys.stderr)
        return 2
    # Live SQLite stays internal; checkpoint is still cheap insurance.
    db = working_dir(cfg) / "screener.db"
    if db.exists():
        subprocess.run(
            ["sqlite3", str(db), "PRAGMA wal_checkpoint(TRUNCATE);"],
            check=False,
            capture_output=True,
        )
    print(f"unmounting {st['mount_point']} ({st['name']})", flush=True)
    result = subprocess.run(
        ["diskutil", "unmount", st["mount_point"]],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        sys.stderr.write(result.stderr or result.stdout or "unmount failed\n")
        return 1
    print(result.stdout.strip() or "unmounted")
    return 0


def backup_disaster(*, yes: bool = False, cfg: dict[str, Any] | None = None) -> int:
    cfg = cfg if cfg is not None else load_config()
    archive = volume_status("archive", cfg)
    disaster = volume_status("disaster", cfg)
    if not disaster["mounted"] or not disaster["root"]:
        print("KINGSTON / disaster volume is not mounted", file=sys.stderr)
        return 1
    if not yes:
        print("refusing to write disaster copy without --yes", file=sys.stderr)
        return 2
    src_root: Path | None = None
    if archive["mounted"] and archive["root"]:
        src_root = Path(archive["root"])
    else:
        src_root = working_dir(cfg)
    dest = Path(disaster["root"])
    dest.mkdir(parents=True, exist_ok=True)
    _rsync(src_root, dest)
    return 0


def format_status(cfg: dict[str, Any] | None = None) -> str:
    cfg = cfg if cfg is not None else load_config()
    lines = [
        f"config: {config_path()}",
        f"working_dir: {working_dir(cfg)}",
        f"archive_dirs_attached: {archive_dirs_attached(cfg)}",
    ]
    for role in ("archive", "disaster"):
        st = volume_status(role, cfg)
        if not st["configured"]:
            lines.append(f"{role}: not configured")
            continue
        state = "mounted" if st["mounted"] else "absent"
        lines.append(
            f"{role}: {st['name']} {state} uuid={st['uuid']} mount={st['mount_point'] or '-'} root={st['root'] or '-'}"
        )
    lines.append("require_archive_jobs: " + ", ".join(sorted(require_archive_jobs(cfg))))
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="nous-storage", description="Nous volume roles")
    sub = parser.add_subparsers(dest="cmd", required=True)

    sub.add_parser("status", help="print working dir and volume state")
    sub.add_parser("ready", help="exit 0 if archive volume is mounted")

    p_check = sub.add_parser("check", help="exit 0 to run a job, 75 to skip")
    p_check.add_argument("job", nargs="?", default="")

    p_eject = sub.add_parser("eject", help="safe-unmount archive volume")
    p_eject.add_argument("--yes", action="store_true")

    p_bk = sub.add_parser("backup-kingston", help="rsync archive tree to disaster volume")
    p_bk.add_argument("--yes", action="store_true")

    p_mig = sub.add_parser("migrate-archive", help="copy archive dirs to DB and replace with symlinks")
    p_mig.add_argument("--yes", action="store_true")

    args = parser.parse_args(argv)
    if args.cmd == "status":
        print(format_status())
        return 0
    if args.cmd == "ready":
        return 0 if archive_ready() else 1
    if args.cmd == "check":
        if args.job and archive_job_should_skip(args.job):
            print(f"SKIP {args.job}: archive volume not mounted")
            return SKIP_EXIT
        return 0
    if args.cmd == "eject":
        return safe_eject(yes=args.yes)
    if args.cmd == "backup-kingston":
        return backup_disaster(yes=args.yes)
    if args.cmd == "migrate-archive":
        return migrate_archive(yes=args.yes)
    parser.error(f"unknown command {args.cmd}")
    return 2


if __name__ == "__main__":
    sys.exit(main())
