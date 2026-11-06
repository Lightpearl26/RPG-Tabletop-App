# -*- coding: utf-8 -*-
"""Safe asset manifests and chunked transfer over the JSON-lines protocol."""
from __future__ import annotations
import base64
from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Any, Iterable

DEFAULT_CHUNK_BYTES = 192 * 1024


@dataclass(frozen=True, slots=True)
class AssetEntry:
    namespace: str
    path: str
    size: int
    sha256: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "namespace": self.namespace,
            "path": self.path,
            "size": self.size,
            "sha256": self.sha256,
        }


class AssetCatalog:
    def __init__(self) -> None:
        self._roots: dict[str, Path] = {}

    def register_root(self, namespace: str, root: str | Path) -> None:
        namespace = str(namespace).strip()
        if not namespace or "/" in namespace or "\\" in namespace:
            raise ValueError("Invalid asset namespace")
        path = Path(root).resolve()
        if not path.is_dir():
            raise FileNotFoundError(path)
        self._roots[namespace] = path

    def manifest(self, namespaces: Iterable[str] | None = None) -> list[AssetEntry]:
        selected = set(namespaces or self._roots)
        result: list[AssetEntry] = []
        for namespace, root in self._roots.items():
            if namespace not in selected:
                continue
            for path in sorted(p for p in root.rglob("*") if p.is_file()):
                raw = path.read_bytes()
                result.append(AssetEntry(
                    namespace, path.relative_to(root).as_posix(), len(raw), sha256(raw).hexdigest()
                ))
        return result

    def resolve(self, namespace: str, relative: str) -> Path:
        if namespace not in self._roots:
            raise KeyError(f"Unknown asset namespace: {namespace}")
        rel = Path(str(relative).replace("\\", "/"))
        if rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe asset path")
        root = self._roots[namespace]
        target = (root / rel).resolve()
        try:
            target.relative_to(root)
        except ValueError as exc:
            raise ValueError("Unsafe asset path") from exc
        if not target.is_file():
            raise FileNotFoundError(target)
        return target

    def chunks(self, namespace: str, relative: str, *, chunk_bytes: int = DEFAULT_CHUNK_BYTES):
        path = self.resolve(namespace, relative)
        raw = path.read_bytes()
        digest = sha256(raw).hexdigest()
        count = max(1, (len(raw) + chunk_bytes - 1) // chunk_bytes)
        for index in range(count):
            piece = raw[index * chunk_bytes:(index + 1) * chunk_bytes]
            yield {
                "namespace": namespace,
                "path": str(relative).replace("\\", "/"),
                "index": index,
                "count": count,
                "file_size": len(raw),
                "sha256": digest,
                "encoding": "base64",
                "data": base64.b64encode(piece).decode("ascii"),
            }


class AssetReceiver:
    def __init__(self, cache_root: str | Path) -> None:
        self.cache_root = Path(cache_root)
        self._buffers: dict[tuple[str, str], dict[int, bytes]] = {}
        self._meta: dict[tuple[str, str], dict[str, Any]] = {}

    def receive_chunk(self, payload: dict[str, Any]) -> Path | None:
        namespace = str(payload.get("namespace", "")).strip()
        relative = str(payload.get("path", "")).replace("\\", "/")
        rel = Path(relative)
        if not namespace or rel.is_absolute() or ".." in rel.parts:
            raise ValueError("Unsafe asset chunk path")
        if payload.get("encoding") != "base64":
            raise ValueError("Unsupported asset chunk encoding")
        index, count = int(payload["index"]), int(payload["count"])
        if index < 0 or count <= 0 or index >= count:
            raise ValueError("Invalid asset chunk index")
        key = (namespace, relative)
        self._buffers.setdefault(key, {})[index] = base64.b64decode(str(payload["data"]).encode("ascii"))
        self._meta[key] = {
            "count": count,
            "sha256": str(payload.get("sha256", "")),
            "file_size": int(payload.get("file_size", 0)),
        }
        if len(self._buffers[key]) != count:
            return None
        raw = b"".join(self._buffers[key][i] for i in range(count))
        meta = self._meta[key]
        if meta["file_size"] and len(raw) != meta["file_size"]:
            raise ValueError("Asset size mismatch")
        if meta["sha256"] and sha256(raw).hexdigest() != meta["sha256"]:
            raise ValueError("Asset SHA-256 mismatch")
        target = self.cache_root / namespace / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(f".{target.name}.part")
        tmp.write_bytes(raw)
        tmp.replace(target)
        self._buffers.pop(key, None)
        self._meta.pop(key, None)
        return target
