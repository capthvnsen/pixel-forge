"""Stable content hashes: sha256 of a canonical encoding. No `hash()`, no `id()`."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel


def content_hash(obj: Any) -> str:
    """sha256 hex digest of a canonical JSON encoding of `obj`.

    Pydantic models are dumped with `mode="json"` first. Raw `bytes` are hashed
    directly (they have no canonical JSON form); everything else is encoded via
    `json.dumps(sort_keys=True, separators=(",", ":"))`, which is stable across
    processes and Python versions.
    """
    if isinstance(obj, bytes):
        return hashlib.sha256(obj).hexdigest()
    payload = obj.model_dump(mode="json") if isinstance(obj, BaseModel) else obj
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def file_hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def short(hash_str: str, n: int = 12) -> str:
    return hash_str[:n]
