"""Content-addressed evidence store: every file a run used is copied once, named by its SHA-256.

A stored file is never overwritten, so the bytes behind a result can always be re-hashed and
compared with the database. Stand-in for Azure Blob Storage.
"""
import hashlib
import shutil
from pathlib import Path
from uuid import UUID

from tickmark.config import settings


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def put(path: Path, tenant_id: UUID, engagement_id: UUID, root: Path | None = None) -> tuple[str, Path]:
    """Copy a file into the engagement's part of the store (once). Returns (sha256, stored path).

    Layout: <root>/<tenant>/<engagement>/<sha[:2]>/<sha>.<ext>. Nothing is shared across
    engagements, so an ethical wall also holds for files."""
    path = Path(path)
    sha = sha256_file(path)
    dest = (Path(root or settings.evidence_dir) / str(tenant_id) / str(engagement_id) / sha[:2]
            / f"{sha}{path.suffix.lower()}")
    if not dest.exists():
        dest.parent.mkdir(parents=True, exist_ok=True)
        tmp = dest.with_suffix(dest.suffix + ".part")
        shutil.copyfile(path, tmp)
        if sha256_file(tmp) != sha:
            tmp.unlink()
            raise OSError(f"copy of {path} changed while storing it")
        tmp.replace(dest)
    return sha, dest
