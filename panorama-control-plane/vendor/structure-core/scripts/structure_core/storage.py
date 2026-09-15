"""UTF-8 storage, contained paths, atomic writes and local process exclusion."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time
import uuid


class StructureError(ValueError):
    pass


def now():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf-8")).hexdigest()


def key(value):
    if not isinstance(value, str) or not value.strip():
        raise StructureError("ID must be a nonempty string")
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def contained(root, value):
    if not isinstance(value, str) or not value:
        raise StructureError("Path must be a nonempty string")
    root = Path(root).resolve()
    target = (root / value).resolve()
    if not target.is_relative_to(root):
        raise StructureError(f"Path escapes project: {value}")
    return target


def find_root(explicit=None):
    from .identity import classify, resolve_project
    if explicit:
        root = Path(explicit).resolve()
        if not root.is_dir():
            raise StructureError(f"Project root is not a directory: {root}")
        identity = classify(root)
        if identity and identity["kind"] == "module":
            return Path(resolve_project(root)["project_root"])
        return root
    start = Path.cwd().resolve()
    for root in (start, *start.parents):
        if (root / ".structure").exists():
            return Path(resolve_project(start)["project_root"])
    # Vendored CLI can be invoked from a different working directory.
    for root in Path(__file__).resolve().parents:
        if root.name == ".structure":
            return root.parent
    raise StructureError("No .structure found; pass --project-root explicitly for initialization")


def read_json(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise StructureError(f"Cannot read JSON {path}: {exc}") from exc


def retry_permission(operation):
    """Retry only temporary Windows sharing failures, not a whole mutation."""
    for attempt in range(8):
        try:
            return operation()
        except PermissionError:
            if os.name != "nt" or attempt == 7:
                raise
            time.sleep(0.05 * (attempt + 1))


def rename_directory(source, target):
    """Windows indexers can briefly open a newly-created directory."""
    retry_permission(lambda: Path(source).rename(target))


def atomic_text(path, text):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        retry_permission(lambda: os.replace(temporary, path))
    finally:
        temporary.unlink(missing_ok=True)


def write_json(path, data, exclusive=False):
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    if not exclusive:
        return atomic_text(path, text)
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    # Caller holds project_mutex. O_EXCL also prevents accidental overwrites.
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(text)
        stream.flush()
        os.fsync(stream.fileno())


@contextmanager
def project_mutex(root, timeout=15):
    """OS-released lock; no stale-lock deletion race after a crashed process.

    Scope is a canonical checkout on one machine, not a distributed lock.
    The file is outside .structure so directory swaps work on Windows too.
    """
    directory = Path(tempfile.gettempdir()) / "structure-protocol-mutex"
    directory.mkdir(exist_ok=True)
    identity = os.path.normcase(str(Path(root).resolve()))
    path = directory / key(identity)
    with path.open("a+b") as stream:
        if path.stat().st_size == 0:
            stream.write(b"0")
            stream.flush()
        deadline = time.monotonic() + timeout
        while True:
            try:
                stream.seek(0)
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.monotonic() >= deadline:
                    raise StructureError("Project is busy; retry after the current operation")
                time.sleep(0.025)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
