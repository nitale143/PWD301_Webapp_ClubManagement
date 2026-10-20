"""Private, verifiable SQLite backups and non-destructive restore drills."""
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import sqlite3
import stat
import tempfile
import uuid
from urllib.parse import quote

from sqlalchemy.engine import make_url


FORMAT_VERSION = 1
DATABASE_FILENAME = "database.sqlite3"
MANIFEST_FILENAME = "manifest.json"
REQUIRED_TABLES = frozenset({"user", "chat_conversation", "payment_evidence"})
_CHUNK_SIZE = 1024 * 1024


class BackupError(ValueError):
    """Raised when a backup cannot be created, verified, or safely restored."""


def _path_from_database(value, *, must_exist):
    """Resolve a filesystem SQLite database path from a path or SQLAlchemy SQLite URL."""
    if hasattr(value, "drivername") and hasattr(value, "database"):
        url = value
    elif isinstance(value, (str, os.PathLike)) and str(value).startswith("sqlite:"):
        try:
            url = make_url(str(value))
        except (TypeError, ValueError) as error:
            raise BackupError("A valid file-backed SQLite database is required.") from error
    else:
        url = None

    if url is not None:
        if url.drivername.split("+", 1)[0] != "sqlite" or not url.database or url.database == ":memory:":
            raise BackupError("Only file-backed SQLite databases can be backed up.")
        path = Path(url.database)
    else:
        if not isinstance(value, (str, os.PathLike)):
            raise BackupError("A filesystem path or SQLAlchemy SQLite URL is required.")
        path = Path(value)

    try:
        resolved = path.resolve(strict=must_exist)
    except (OSError, RuntimeError) as error:
        raise BackupError(f"Database path cannot be resolved: {path}") from error
    if must_exist and not resolved.is_file():
        raise BackupError("The source database must be an existing regular file.")
    if not must_exist and (resolved.name in {"", ".", ".."} or resolved == resolved.parent):
        raise BackupError("The restore target must be an explicitly named database file.")
    return resolved


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while True:
            chunk = source.read(_CHUNK_SIZE)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _schema_info(connection):
    """Return the database's concrete schema objects, excluding SQLite internals."""
    rows = connection.execute(
        "SELECT type, name, tbl_name, sql FROM sqlite_master "
        "WHERE type IN ('table', 'index', 'view', 'trigger') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
    ).fetchall()
    objects = [[row[0], row[1], row[2], row[3]] for row in rows]
    tables = {row[1] for row in objects if row[0] == "table"}
    missing = REQUIRED_TABLES - tables
    if missing:
        raise BackupError("Database schema is missing required tables: " + ", ".join(sorted(missing)))
    return objects


def _check_database(database_path, expected_schema=None):
    uri = "file:" + quote(str(database_path), safe="/:") + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True, timeout=30)
    except sqlite3.Error as error:
        raise BackupError(f"Could not open SQLite database: {error}") from error
    try:
        integrity_rows = [row[0] for row in connection.execute("PRAGMA integrity_check")]
        if integrity_rows != ["ok"]:
            raise BackupError("SQLite integrity check failed: " + "; ".join(map(str, integrity_rows[:5])))
        foreign_key_issues = connection.execute("PRAGMA foreign_key_check").fetchall()
        if foreign_key_issues:
            raise BackupError(f"SQLite foreign-key check found {len(foreign_key_issues)} violation(s).")
        schema = _schema_info(connection)
        if expected_schema is not None and schema != expected_schema:
            raise BackupError("Backup database schema does not match its manifest.")
        return schema
    except sqlite3.Error as error:
        raise BackupError(f"Could not inspect SQLite database: {error}") from error
    finally:
        connection.close()


def _private_directory(path):
    path = Path(path).expanduser()
    if path.is_symlink():
        raise BackupError(f"Backup directory must not be a symbolic link: {path}")
    try:
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not path.is_dir():
            raise BackupError(f"Backup path is not a directory: {path}")
        os.chmod(path, 0o700)
        return path.resolve(strict=True)
    except OSError as error:
        raise BackupError(f"Could not prepare private backup directory: {error}") from error


def _sqlite_backup(source_path, destination_path):
    source_uri = "file:" + quote(str(source_path), safe="/:") + "?mode=ro"
    source = None
    destination = None
    try:
        source = sqlite3.connect(source_uri, uri=True, timeout=30)
        destination = sqlite3.connect(str(destination_path), timeout=30)
        source.backup(destination, pages=256, sleep=0.05)
        destination.commit()
    except sqlite3.Error as error:
        raise BackupError(f"SQLite backup failed: {error}") from error
    finally:
        if destination is not None:
            destination.close()
        if source is not None:
            source.close()
    os.chmod(destination_path, 0o600)


def _source_tree(path, kind):
    if path is None:
        return None
    raw_path = Path(path).expanduser()
    if raw_path.is_symlink():
        raise BackupError(f"{kind} directory must not be a symbolic link: {raw_path}")
    try:
        source = raw_path.resolve(strict=False)
    except (OSError, RuntimeError) as error:
        raise BackupError(f"Could not resolve {kind} directory: {error}") from error
    if os.path.lexists(source) and not source.is_dir():
        raise BackupError(f"{kind} path is not a directory: {source}")
    return source


def _regular_files(directory):
    """Yield regular files without traversing symlinks."""
    if not directory.exists():
        return
    for current, directories, files in os.walk(directory, followlinks=False):
        current_path = Path(current)
        for name in list(directories):
            child = current_path / name
            if child.is_symlink():
                raise BackupError(f"Upload directory contains a symbolic link: {child}")
        directories.sort()
        for name in sorted(files):
            source = current_path / name
            try:
                mode = source.lstat().st_mode
            except OSError as error:
                raise BackupError(f"Could not read upload file: {source}") from error
            if stat.S_ISLNK(mode) or not stat.S_ISREG(mode):
                raise BackupError(f"Upload directory contains a non-regular file: {source}")
            yield source.relative_to(directory), source


def _copy_regular_file(source, destination):
    destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(source, flags)
        with os.fdopen(descriptor, "rb") as reader:
            if not stat.S_ISREG(os.fstat(reader.fileno()).st_mode):
                raise BackupError(f"Source is not a regular file: {source}")
            with destination.open("xb") as writer:
                shutil.copyfileobj(reader, writer, _CHUNK_SIZE)
                writer.flush()
                os.fsync(writer.fileno())
        os.chmod(destination, 0o600)
    except FileExistsError:
        raise BackupError(f"Refusing to overwrite a file: {destination}") from None
    except OSError as error:
        raise BackupError(f"Could not copy file {source}: {error}") from error


def create_backup(source_db, backup_root, *, avatar_dir=None, evidence_dir=None):
    """Create a private point-in-time SQLite backup and checksummed upload manifest.

    `source_db` accepts a filesystem path or SQLAlchemy file-backed SQLite URL. Upload
    directories are optional and copied under the backup's `assets/` directory.
    """
    source_path = _path_from_database(source_db, must_exist=True)
    source_directories = {
        "avatars": _source_tree(avatar_dir, "avatar"),
        "payment_evidence": _source_tree(evidence_dir, "payment evidence"),
    }
    prospective_root = Path(backup_root).expanduser().resolve(strict=False)
    for source_directory in source_directories.values():
        if source_directory and prospective_root.is_relative_to(source_directory):
            raise BackupError("Backup directory must not be inside a source upload directory.")
    root = _private_directory(backup_root)
    backup_name = "club-backup-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8]
    final_path = root / backup_name
    stage = Path(tempfile.mkdtemp(prefix=".backup-stage-", dir=root))
    os.chmod(stage, 0o700)
    try:
        database_path = stage / DATABASE_FILENAME
        _sqlite_backup(source_path, database_path)
        schema = _check_database(database_path)
        artifact_roots = {}
        artifacts = []
        for kind, source_directory in source_directories.items():
            if source_directory is None:
                continue
            backup_directory = stage / "assets" / kind
            backup_directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            artifact_roots[kind] = str(source_directory)
            for relative, source_file in _regular_files(source_directory):
                relative_posix = PurePosixPath(*relative.parts).as_posix()
                archive_name = f"assets/{kind}/{relative_posix}"
                destination = stage.joinpath(*PurePosixPath(archive_name).parts)
                _copy_regular_file(source_file, destination)
                artifacts.append({
                    "kind": kind,
                    "path": archive_name,
                    "sha256": _sha256(destination),
                    "size_bytes": destination.stat().st_size,
                })

        manifest = {
            "format_version": FORMAT_VERSION,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "source_database": str(source_path),
            "database": {
                "file": DATABASE_FILENAME,
                "sha256": _sha256(database_path),
                "size_bytes": database_path.stat().st_size,
            },
            "schema": schema,
            "schema_sha256": hashlib.sha256(_canonical_json(schema).encode("utf-8")).hexdigest(),
            "artifact_roots": artifact_roots,
            "artifacts": artifacts,
        }
        manifest_path = stage / MANIFEST_FILENAME
        with manifest_path.open("x", encoding="utf-8") as handle:
            json.dump(manifest, handle, ensure_ascii=False, sort_keys=True, indent=2)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(manifest_path, 0o600)
        os.rename(stage, final_path)
        return final_path
    except Exception:
        if stage.exists():
            shutil.rmtree(stage)
        raise


def _backup_file(root, relative_path):
    if not isinstance(relative_path, str):
        raise BackupError("Backup manifest contains an invalid file path.")
    parsed = PurePosixPath(relative_path)
    if parsed.is_absolute() or not parsed.parts or any(part in {"", ".", ".."} for part in parsed.parts):
        raise BackupError("Backup manifest contains an unsafe file path.")
    candidate = root.joinpath(*parsed.parts)
    current = root
    for part in parsed.parts:
        current = current / part
        if current.is_symlink():
            raise BackupError("Backup contents must not contain symbolic links.")
    try:
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as error:
        raise BackupError(f"Backup file is missing: {relative_path}") from error
    if not resolved.is_file() or not resolved.is_relative_to(root):
        raise BackupError(f"Backup file is not a regular file: {relative_path}")
    return resolved


def _load_and_verify(backup_path):
    root = Path(backup_path).expanduser()
    if root.is_symlink() or not root.is_dir():
        raise BackupError("Backup must be an existing directory.")
    try:
        root = root.resolve(strict=True)
        manifest_path = _backup_file(root, MANIFEST_FILENAME)
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise BackupError(f"Could not read backup manifest: {error}") from error
    if not isinstance(manifest, dict) or manifest.get("format_version") != FORMAT_VERSION:
        raise BackupError("Unsupported or invalid backup manifest.")
    if not isinstance(manifest.get("created_at"), str):
        raise BackupError("Backup manifest is missing its creation timestamp.")
    source_database = manifest.get("source_database")
    if not isinstance(source_database, str) or not source_database:
        raise BackupError("Backup manifest is missing its source database path.")
    database_metadata = manifest.get("database")
    if not isinstance(database_metadata, dict) or database_metadata.get("file") != DATABASE_FILENAME:
        raise BackupError("Backup manifest does not name the expected SQLite database.")
    database_path = _backup_file(root, DATABASE_FILENAME)
    if (database_path.stat().st_size != database_metadata.get("size_bytes")
            or not _constant_time_equal(_sha256(database_path), database_metadata.get("sha256"))):
        raise BackupError("Backup database checksum does not match its manifest.")

    schema = manifest.get("schema")
    if not isinstance(schema, list):
        raise BackupError("Backup manifest is missing its schema snapshot.")
    schema_digest = hashlib.sha256(_canonical_json(schema).encode("utf-8")).hexdigest()
    if not _constant_time_equal(schema_digest, manifest.get("schema_sha256")):
        raise BackupError("Backup schema checksum does not match its manifest.")
    actual_schema = _check_database(database_path, expected_schema=schema)

    roots = manifest.get("artifact_roots")
    artifacts = manifest.get("artifacts")
    if not isinstance(roots, dict) or not isinstance(artifacts, list):
        raise BackupError("Backup manifest has invalid upload metadata.")
    allowed_kinds = {"avatars", "payment_evidence"}
    if (set(roots) - allowed_kinds
            or any(not isinstance(kind, str) or not isinstance(source, str) or not source
                   for kind, source in roots.items())):
        raise BackupError("Backup manifest contains an unsupported upload directory.")
    expected_paths = {DATABASE_FILENAME, MANIFEST_FILENAME}
    seen_paths = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict) or artifact.get("kind") not in roots:
            raise BackupError("Backup manifest contains invalid upload metadata.")
        relative = artifact.get("path")
        file_path = _backup_file(root, relative)
        if not relative.startswith(f"assets/{artifact['kind']}/") or relative in seen_paths:
            raise BackupError("Backup manifest contains an unsafe or duplicate upload path.")
        seen_paths.add(relative)
        expected_paths.add(relative)
        if (file_path.stat().st_size != artifact.get("size_bytes")
                or not _constant_time_equal(_sha256(file_path), artifact.get("sha256"))):
            raise BackupError(f"Upload checksum does not match its manifest: {relative}")

    actual_paths = set()
    for current, directories, files in os.walk(root, followlinks=False):
        current_path = Path(current)
        for name in directories:
            if (current_path / name).is_symlink():
                raise BackupError("Backup contents must not contain symbolic links.")
        for name in files:
            path = current_path / name
            if path.is_symlink() or not path.is_file():
                raise BackupError("Backup contents must only contain regular files.")
            actual_paths.add(path.relative_to(root).as_posix())
    if actual_paths != expected_paths:
        raise BackupError("Backup contains files that are missing from or absent in its manifest.")

    return root, manifest, database_path, actual_schema


def _constant_time_equal(left, right):
    return isinstance(left, str) and isinstance(right, str) and hmac.compare_digest(left, right)


def verify_backup(backup_path):
    """Verify manifest checksums, SQLite integrity, foreign keys, and schema."""
    _root, manifest, _database_path, schema = _load_and_verify(backup_path)
    return {
        "valid": True,
        "created_at": manifest["created_at"],
        "tables": sum(1 for item in schema if item[0] == "table"),
        "artifacts": len(manifest["artifacts"]),
        "source_database": manifest["source_database"],
    }


def _is_same_or_within(path, directory):
    try:
        path.relative_to(directory)
        return True
    except ValueError:
        return False


def _assert_restore_destination(target, source_paths, *, backup_root, label):
    resolved = target.resolve(strict=False)
    if resolved == backup_root or _is_same_or_within(resolved, backup_root):
        raise BackupError(f"{label} must be outside the backup directory.")
    for source in source_paths:
        if source is None:
            continue
        source = Path(source).resolve(strict=False)
        if resolved == source or _is_same_or_within(resolved, source):
            raise BackupError(f"{label} must not overlap the live source data.")
        if _is_same_or_within(source, resolved):
            raise BackupError(f"{label} must not contain the live source data.")
    return resolved


def restore_backup(backup_path, target_db, *, target_artifacts_dir=None):
    """Restore a verified backup to new, explicitly named drill targets only.

    Existing files and directories are never overwritten. Uploads are restored under
    `avatars/` and `payment_evidence/` inside the optional artifact target directory.
    """
    root, manifest, database_path, _schema = _load_and_verify(backup_path)
    target_path = _path_from_database(target_db, must_exist=False)
    source_paths = [manifest["source_database"], *manifest["artifact_roots"].values()]
    target_path = _assert_restore_destination(target_path, source_paths,
                                              backup_root=root, label="Restore database target")
    if os.path.lexists(target_path):
        raise BackupError("Restore database target already exists; refusing to overwrite it.")
    if not target_path.parent.is_dir():
        raise BackupError("Restore database target parent directory must already exist.")

    artifact_target = None
    if target_artifacts_dir is not None:
        raw_artifact_target = Path(target_artifacts_dir).expanduser()
        if raw_artifact_target.is_symlink():
            raise BackupError("Restore artifact target must not be a symbolic link.")
        artifact_target = _assert_restore_destination(
            raw_artifact_target, source_paths, backup_root=root, label="Restore artifact target")
        if os.path.lexists(artifact_target):
            raise BackupError("Restore artifact target already exists; refusing to overwrite it.")
        if not artifact_target.parent.is_dir():
            raise BackupError("Restore artifact target parent directory must already exist.")
        if artifact_target == target_path or _is_same_or_within(target_path, artifact_target):
            raise BackupError("Database and upload restore targets must be separate.")
        if _is_same_or_within(artifact_target, target_path.parent / target_path.name):
            raise BackupError("Database and upload restore targets must be separate.")
    elif manifest["artifacts"]:
        # A DB-only drill is allowed; verify_backup has already verified the uploaded files.
        pass

    descriptor, temporary_name = tempfile.mkstemp(prefix=".restore-", suffix=".sqlite3",
                                                  dir=target_path.parent)
    temporary_db = Path(temporary_name)
    os.chmod(temporary_db, 0o600)
    assets_created = False
    database_created = False
    try:
        with os.fdopen(descriptor, "wb") as writer, database_path.open("rb") as reader:
            shutil.copyfileobj(reader, writer, _CHUNK_SIZE)
            writer.flush()
            os.fsync(writer.fileno())
        _check_database(temporary_db, expected_schema=manifest["schema"])

        if artifact_target is not None:
            artifact_target.mkdir(mode=0o700)
            assets_created = True
            for kind in manifest["artifact_roots"]:
                (artifact_target / kind).mkdir(mode=0o700, parents=True, exist_ok=True)
            for item in manifest["artifacts"]:
                source = _backup_file(root, item["path"])
                relative = PurePosixPath(item["path"]).relative_to(PurePosixPath("assets") / item["kind"])
                destination = artifact_target / item["kind"] / Path(*relative.parts)
                _copy_regular_file(source, destination)

        # Hard-linking the staged file provides an atomic no-overwrite install.
        os.link(temporary_db, target_path)
        database_created = True
        temporary_db.unlink()
        return target_path
    except FileExistsError:
        raise BackupError("A restore target appeared during the operation; refusing to overwrite it.") from None
    except OSError as error:
        raise BackupError(f"Restore drill failed: {error}") from error
    finally:
        if temporary_db.exists():
            temporary_db.unlink()
        if not database_created and assets_created and artifact_target is not None:
            shutil.rmtree(artifact_target, ignore_errors=True)
