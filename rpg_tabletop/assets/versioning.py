# -*- coding: utf-8 -*-
"""
Version history of a campaign's assets, git-like, with the app's vocabulary.

    git                 here
    ------------------  ---------------------------------------------------
    repository (.git)   <campaign>/.versions/
    main                the campaign
    branch              a session (created from the campaign or a session)
    HEAD / checkout     the current session; switching rewrites the files
    commit              a point (saved automatically before every operation)
    push                send a session to the campaign: the campaign becomes
                        exactly that session
    pull                take the campaign into a session: three-way merge,
                        the campaign wins every conflict
    merge               session into session: three-way merge, conflicts are
                        resolved by the user (one version per asset)
    reset --hard        go back to a point (the later points are dropped)
    branch <point>      new session from any point of the history

The working files are the campaign folder itself (characters/, entities/,
items/, spells/, maps/): the IDE, the runtime and the network server keep
reading them through `Campaign` and always see the current session. Session
journals (sessions/), campaign.json and hidden files are not versioned.

Storage (content-addressed, like git): objects/<sha256> holds file contents
and trees (path -> content hash), points/<id>.json the points, refs.json the
tip of the campaign and of every session, HEAD the current session.

Merges work per *asset*: one JSON file, or one map folder (map.json, tiles,
media and preview together). When both sides changed the same JSON asset, the
change is merged field by field; only a field changed differently on both
sides is a conflict.
"""
from __future__ import annotations

import json
import os
import shutil
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

VERSIONS_DIR = ".versions"
TRACKED_DIRS = ("characters", "entities", "items", "spells", "maps")
CAMPAIGN = ""  # the campaign's own line of history ("main"); sessions have names

Tree = dict[str, str]  # relative posix path -> content hash


class VersioningError(Exception):
    """An operation that cannot be done (unknown session, name taken...)."""


@dataclass(frozen=True)
class Point:
    """One saved state of the assets (a commit)."""

    id: str
    tree: str
    parents: tuple[str, ...]
    message: str
    time: str
    session: str  # CAMPAIGN for the campaign
    meta: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "tree": self.tree, "parents": list(self.parents), "message": self.message,
            "time": self.time, "session": self.session, "meta": self.meta,
        }


@dataclass
class Conflict:
    """An asset both sides changed in incompatible ways."""

    asset: str
    ours: Tree      # files of the asset on the receiving side ({} = deleted)
    theirs: Tree    # files of the asset on the incoming side ({} = deleted)
    fields: list[str] = field(default_factory=list)  # JSON fields in conflict, if JSON


@dataclass
class Changes:
    """What an operation changed in the working files."""

    paths: set[str] = field(default_factory=set)

    @property
    def assets(self) -> set[str]:
        return {asset_of(path) for path in self.paths}

    def resources(self) -> set[tuple[str, str]]:
        """(resource type, resource id) of every changed asset, as the IDE names them."""
        result = set()
        for asset in self.assets:
            parts = asset.split("/")
            if parts[0] == "maps" and len(parts) >= 2:
                result.add(("maps", parts[1]))
            elif len(parts) == 2:
                result.add((parts[0], Path(parts[1]).stem))
        return result


# Resolution of conflicts between two sessions: asset -> "ours" | "theirs";
# None cancels the merge
Resolver = Callable[[list[Conflict]], Optional[dict[str, str]]]


def asset_of(path: str) -> str:
    """The asset a file belongs to: a map folder, otherwise the file itself."""
    parts = path.split("/")
    if parts[0] == "maps" and len(parts) >= 3 and parts[1] != "media":
        return f"maps/{parts[1]}"
    return path


def _group(tree: Tree) -> dict[str, Tree]:
    groups: dict[str, Tree] = {}
    for path, digest in tree.items():
        groups.setdefault(asset_of(path), {})[path] = digest
    return groups


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# =============================================================================
# JSON merge
# =============================================================================
_MISSING = object()


def merge_json(
    base: Any, ours: Any, theirs: Any, path: str = "", *, prefer: str = "ours"
) -> tuple[Any, list[str]]:
    """
    Three-way merge of JSON values. Objects are merged key by key; any other
    value (lists included) is taken whole. Returns (merged, conflicting paths);
    a conflicting key takes the `prefer` side ("ours" or "theirs").
    """
    if ours == theirs:
        return ours, []
    if ours == base:
        return theirs, []
    if theirs == base:
        return ours, []
    if isinstance(ours, dict) and isinstance(theirs, dict):
        base_dict = base if isinstance(base, dict) else {}
        merged: dict[str, Any] = {}
        conflicts: list[str] = []
        for key in list(dict.fromkeys([*ours, *theirs])):
            value, sub = merge_json(
                base_dict.get(key, _MISSING), ours.get(key, _MISSING), theirs.get(key, _MISSING),
                f"{path}.{key}" if path else str(key), prefer=prefer,
            )
            conflicts.extend(sub)
            if value is not _MISSING:
                merged[key] = value
        return merged, conflicts
    return (theirs if prefer == "theirs" else ours), [path or "(fichier entier)"]


# =============================================================================
# Store
# =============================================================================
class VersionStore:
    """The `.versions` folder of one campaign."""

    def __init__(self, root: str | Path) -> None:
        self.root = Path(root)
        self.dir = self.root / VERSIONS_DIR

    # ------------------------------------------------------------- setup
    @property
    def exists(self) -> bool:
        return (self.dir / "refs.json").is_file()

    def init(self) -> bool:
        """Create the history with a first point of the current files. False if it exists."""
        if self.exists:
            return False
        for name in ("objects", "points"):
            (self.dir / name).mkdir(parents=True, exist_ok=True)
        self._write_json(self.dir / "refs.json", {"campaign": None, "sessions": {}})
        self._write_json(self.dir / "HEAD.json", {"session": CAMPAIGN})
        self.save_point("Version initiale de la campagne")
        return True

    # ----------------------------------------------------------- objects
    def _object_path(self, digest: str) -> Path:
        return self.dir / "objects" / digest[:2] / digest[2:]

    def _store_bytes(self, data: bytes) -> str:
        digest = sha256(data).hexdigest()
        path = self._object_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            tmp.write_bytes(data)
            os.replace(tmp, path)
        return digest

    def _store_file(self, source: Path, digest: str) -> None:
        path = self._object_path(digest)
        if not path.exists():
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")
            # A copy, never a hard link: a file changed in place (an image edited
            # outside the app) would silently change the history too
            shutil.copyfile(source, tmp)
            os.replace(tmp, path)

    def read_object(self, digest: str) -> bytes:
        return self._object_path(digest).read_bytes()

    def _save_tree(self, tree: Tree) -> str:
        return self._store_bytes(json.dumps(tree, sort_keys=True, separators=(",", ":")).encode("utf-8"))

    def tree(self, point_id: str | None) -> Tree:
        if not point_id:
            return {}
        return json.loads(self.read_object(self.point(point_id).tree))

    # ------------------------------------------------------------ points
    def point(self, point_id: str) -> Point:
        path = self.dir / "points" / f"{point_id}.json"
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise VersioningError(f"Point inconnu : {point_id}") from exc
        return Point(point_id, data["tree"], tuple(data.get("parents", [])), data.get("message", ""),
                     data.get("time", ""), data.get("session", CAMPAIGN), dict(data.get("meta", {})))

    def _new_point(self, tree_id: str, parents: Iterable[str | None], message: str,
                   session: str, meta: dict[str, Any] | None = None) -> str:
        content = {
            "tree": tree_id, "parents": [p for p in parents if p], "message": message,
            "time": _now(), "session": session, "meta": dict(meta or {}),
        }
        raw = json.dumps(content, sort_keys=True).encode("utf-8")
        point_id = sha256(raw).hexdigest()[:20]
        self._write_json(self.dir / "points" / f"{point_id}.json", content)
        return point_id

    def history(self, session: str = CAMPAIGN, limit: int = 500) -> list[Point]:
        """Points of a session, newest first (following first parents)."""
        result: list[Point] = []
        point_id = self.tip(session)
        while point_id and len(result) < limit:
            point = self.point(point_id)
            result.append(point)
            point_id = point.parents[0] if point.parents else None
        return result

    # -------------------------------------------------------------- refs
    def _refs(self) -> dict[str, Any]:
        return json.loads((self.dir / "refs.json").read_text(encoding="utf-8"))

    def _set_tip(self, session: str, point_id: str) -> None:
        refs = self._refs()
        if session == CAMPAIGN:
            refs["campaign"] = point_id
        else:
            refs["sessions"][session] = point_id
        self._write_json(self.dir / "refs.json", refs)

    def tip(self, session: str = CAMPAIGN) -> str | None:
        refs = self._refs()
        if session == CAMPAIGN:
            return refs.get("campaign")
        if session not in refs["sessions"]:
            raise VersioningError(f"Session inconnue : {session}")
        return refs["sessions"][session]

    def sessions(self) -> list[str]:
        return sorted(self._refs()["sessions"])

    def has_session(self, name: str) -> bool:
        return name in self._refs()["sessions"]

    def current(self) -> str:
        """The current session, CAMPAIGN when the files are the campaign's."""
        try:
            return str(json.loads((self.dir / "HEAD.json").read_text(encoding="utf-8")).get("session", ""))
        except (OSError, ValueError):
            return CAMPAIGN

    def _set_current(self, session: str) -> None:
        self._write_json(self.dir / "HEAD.json", {"session": session})

    # ----------------------------------------------------- working files
    def scan(self) -> Tree:
        """
        Hash the working files (content stored as it goes). A file whose size
        and modification time did not change is not read again (index.json).
        """
        index_path = self.dir / "index.json"
        try:
            index = json.loads(index_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            index = {}
        tree: Tree = {}
        new_index: dict[str, list[Any]] = {}
        for top in TRACKED_DIRS:
            base = self.root / top
            if not base.is_dir():
                continue
            for dirpath, dirnames, filenames in os.walk(base):
                dirnames[:] = sorted(name for name in dirnames if not name.startswith("."))
                for name in filenames:
                    if name.startswith("."):
                        continue
                    full = Path(dirpath) / name
                    rel = full.relative_to(self.root).as_posix()
                    try:
                        stat = full.stat()
                    except OSError:
                        continue
                    cached = index.get(rel)
                    if cached and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size \
                            and self._object_path(cached[2]).exists():
                        digest = cached[2]
                    else:
                        digest = sha256(full.read_bytes()).hexdigest()
                        self._store_file(full, digest)
                    tree[rel] = digest
                    new_index[rel] = [stat.st_mtime_ns, stat.st_size, digest]
        self._write_json(index_path, new_index)
        return tree

    def _write_tree(self, target: Tree, current: Tree) -> Changes:
        """Make the working files equal to `target` (current: what they are now)."""
        changes = Changes()
        for rel in sorted(set(current) - set(target)):
            (self.root / rel).unlink(missing_ok=True)
            changes.paths.add(rel)
        for rel, digest in target.items():
            if current.get(rel) == digest:
                continue
            path = self.root / rel
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.vtmp")
            tmp.write_bytes(self.read_object(digest))
            os.replace(tmp, path)
            changes.paths.add(rel)
        self._prune_empty_dirs({str(Path(rel).parent) for rel in changes.paths})
        self.scan()  # refresh the index with the new modification times
        return changes

    def _prune_empty_dirs(self, folders: set[str]) -> None:
        for folder in sorted(folders, key=len, reverse=True):
            path = self.root / folder
            while path != self.root and path.name not in TRACKED_DIRS and path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    break
                path = path.parent

    # ----------------------------------------------------- basic operations
    def save_point(self, message: str = "", *, meta: dict[str, Any] | None = None,
                   force: bool = False) -> str | None:
        """
        Record the working files as a point of the current session. Returns its
        id, None when nothing changed since the last point (unless `force`).
        """
        session = self.current()
        tree_id = self._save_tree(self.scan())
        tip = self.tip(session)
        if tip and not force and self.point(tip).tree == tree_id:
            return None
        point_id = self._new_point(tree_id, [tip], message or "Point automatique", session, meta)
        self._set_tip(session, point_id)
        return point_id

    def create_session(self, name: str, *, source: str | None = None, point: str | None = None,
                       switch: bool = False) -> str:
        """
        New session starting from `point`, or from the tip of `source` (a session
        name, CAMPAIGN; default: the current one). Returns its first point.
        """
        name = str(name).strip()
        if not name:
            raise VersioningError("Le nom de la session est vide.")
        if self.has_session(name):
            raise VersioningError(f"La session « {name} » existe déjà.")
        source = self.current() if source is None else source
        if point is None:
            if source == self.current():
                self.save_point(f"Avant la création de « {name} »")
            point = self.tip(source)
        else:
            self.point(point)  # must exist
        start = self._new_point(self.point(point).tree, [point], f"Création depuis {_label(source)}", name)
        self._set_tip(name, start)
        if switch:
            self.switch(name)
        return start

    def delete_session(self, name: str) -> None:
        if self.current() == name:
            raise VersioningError("Impossible de supprimer la session courante : change d'abord de session.")
        refs = self._refs()
        refs["sessions"].pop(name, None)
        self._write_json(self.dir / "refs.json", refs)

    def rename_session(self, old: str, new: str) -> None:
        refs = self._refs()
        if old not in refs["sessions"]:
            raise VersioningError(f"Session inconnue : {old}")
        if new in refs["sessions"]:
            raise VersioningError(f"La session « {new} » existe déjà.")
        refs["sessions"][new] = refs["sessions"].pop(old)
        self._write_json(self.dir / "refs.json", refs)
        if self.current() == old:
            self._set_current(new)

    def switch(self, session: str) -> Changes:
        """Save the current files, then rewrite them as `session` (checkout)."""
        target_tip = self.tip(session)  # raises for an unknown session
        current = self.current()
        self.save_point(f"Avant de passer à {_label(session)}")
        if session == current:
            return Changes()
        changes = self._write_tree(self.tree(target_tip), self.tree(self.tip(current)))
        self._set_current(session)
        return changes

    def reset(self, session: str, point_id: str) -> Changes:
        """
        Bring a session back to one of its points; the later points are dropped
        from it (they stay reachable from other sessions that contain them).
        """
        if point_id not in {point.id for point in self.history(session, limit=100000)}:
            raise VersioningError("Ce point ne fait pas partie de cette session.")
        if self.current() == session:
            self.save_point("Avant le retour en arrière")
            before = self.tree(self.tip(session))
            self._set_tip(session, point_id)
            return self._write_tree(self.tree(point_id), before)
        self._set_tip(session, point_id)
        return Changes()

    # ------------------------------------------------------------ merges
    def merge_base(self, first: str, second: str) -> str | None:
        """The nearest common ancestor of two points."""
        ancestors = set()
        queue = deque([first])
        while queue:
            point_id = queue.popleft()
            if point_id in ancestors:
                continue
            ancestors.add(point_id)
            queue.extend(self.point(point_id).parents)
        queue = deque([second])
        seen = set()
        while queue:
            point_id = queue.popleft()
            if point_id in ancestors:
                return point_id
            if point_id in seen:
                continue
            seen.add(point_id)
            queue.extend(self.point(point_id).parents)
        return None

    def push(self, session: str) -> Changes:
        """Send a session to the campaign: the campaign becomes exactly that session."""
        if session == CAMPAIGN:
            raise VersioningError("Choisis une session à envoyer dans la campagne.")
        if self.current() == session:
            self.save_point("Avant l'envoi dans la campagne")
        source_tip = self.tip(session)
        campaign_tip = self.tip(CAMPAIGN)
        tree_id = self.point(source_tip).tree
        point_id = self._new_point(tree_id, [campaign_tip, source_tip], f"Envoi de « {session} »", CAMPAIGN)
        before = self.tree(campaign_tip)
        self._set_tip(CAMPAIGN, point_id)
        if self.current() == CAMPAIGN:
            return self._write_tree(self.tree(point_id), before)
        return Changes()

    def pull(self, session: str) -> tuple[Changes, list[Conflict]]:
        """
        Take the campaign into a session. Changes made on one side only are
        kept; a conflict keeps the campaign's version. Returns the changes to
        the working files and the conflicts that were decided for the campaign.
        """
        if session == CAMPAIGN:
            raise VersioningError("La campagne n'a rien à récupérer d'elle-même.")
        return self._merge_into(session, CAMPAIGN, None, "Récupération de la campagne", prefer="theirs")

    def merge(self, source: str, target: str, resolve: Resolver | None = None) -> tuple[Changes, list[Conflict]]:
        """
        Merge `source` into `target`. Into the campaign it is a push, from the
        campaign a pull; between two sessions `resolve` picks a version for
        each conflicting asset (the merge is cancelled if it returns None).
        """
        if source == target:
            raise VersioningError("Une session ne se fusionne pas avec elle-même.")
        if target == CAMPAIGN:
            return self.push(source), []
        if source == CAMPAIGN:
            return self.pull(target)
        if resolve is None:
            raise VersioningError("Une fusion entre sessions demande de résoudre les conflits.")
        return self._merge_into(target, source, resolve, f"Fusion de « {source} »")

    def preview_merge(self, source: str, target: str) -> list[Conflict]:
        """The conflicts a merge of `source` into `target` would raise (nothing is changed)."""
        if target == self.current() or source == self.current():
            self.save_point("Avant l'analyse d'une fusion")
        ours, theirs = self.tip(target), self.tip(source)
        base = self.merge_base(ours, theirs)
        _tree, conflicts = self._three_way(self.tree(base), self.tree(ours), self.tree(theirs))
        return conflicts

    def _merge_into(self, target: str, source: str, resolve: Resolver | None, message: str,
                    *, prefer: str | None = None) -> tuple[Changes, list[Conflict]]:
        """
        Three-way merge of `source` into `target`. With `prefer`, conflicts are
        decided for that side (field by field in JSON) and only reported;
        otherwise `resolve` chooses a whole version for each conflicting asset.
        """
        if self.current() in (target, source):
            self.save_point("Avant la fusion")
        ours_tip, theirs_tip = self.tip(target), self.tip(source)
        base = self.merge_base(ours_tip, theirs_tip)
        ours_tree = self.tree(ours_tip)
        merged, conflicts = self._three_way(self.tree(base), ours_tree, self.tree(theirs_tip), prefer=prefer)
        if conflicts and prefer is None:
            if resolve is None:
                raise VersioningError("Des conflits demandent un choix.")
            choices = resolve(conflicts)
            if choices is None:
                raise VersioningError("Fusion annulée.")
            for conflict in conflicts:
                side = conflict.theirs if choices.get(conflict.asset) == "theirs" else conflict.ours
                for path in [p for p in merged if asset_of(p) == conflict.asset]:
                    del merged[path]
                merged.update(side)
        tree_id = self._save_tree(merged)
        if tree_id == self.point(ours_tip).tree and base == theirs_tip:
            return Changes(), conflicts  # already up to date
        point_id = self._new_point(tree_id, [ours_tip, theirs_tip], message, target)
        self._set_tip(target, point_id)
        if self.current() == target:
            return self._write_tree(merged, ours_tree), conflicts
        return Changes(), conflicts

    def _three_way(self, base: Tree, ours: Tree, theirs: Tree,
                   *, prefer: str | None = None) -> tuple[Tree, list[Conflict]]:
        base_groups, our_groups, their_groups = _group(base), _group(ours), _group(theirs)
        merged: Tree = {}
        conflicts: list[Conflict] = []
        for asset in sorted(set(base_groups) | set(our_groups) | set(their_groups)):
            b, o, t = base_groups.get(asset, {}), our_groups.get(asset, {}), their_groups.get(asset, {})
            if o == t or t == b:
                merged.update(o)
                continue
            if o == b:
                merged.update(t)
                continue
            joined = self._merge_json_asset(asset, b, o, t, prefer=prefer or "ours")
            if joined is not None and not joined[1]:
                merged.update(joined[0])
                continue
            conflicts.append(Conflict(asset, o, t, joined[1] if joined is not None else []))
            if prefer is None:
                merged.update(o)  # replaced by the chosen version
            elif joined is not None and (o and t):
                merged.update(joined[0])  # field by field, conflicts for `prefer`
            else:
                merged.update(t if prefer == "theirs" else o)
        return merged, conflicts

    def _merge_json_asset(self, asset: str, base: Tree, ours: Tree, theirs: Tree, *, prefer: str = "ours"):
        """
        Field merge of a JSON asset changed on both sides: (files, conflicting
        fields), the conflicting fields set to the `prefer` side. None when the
        asset is not a JSON file present on both sides.
        """
        if not asset.endswith(".json") or len(ours) != 1 or len(theirs) != 1:
            return None
        try:
            load = lambda tree: json.loads(self.read_object(next(iter(tree.values()))).decode("utf-8")) if tree else {}
            merged, conflicts = merge_json(load(base), load(ours), load(theirs), prefer=prefer)
        except (ValueError, UnicodeDecodeError):
            return None
        digest = self._store_bytes(json.dumps(merged, ensure_ascii=False, indent=4).encode("utf-8"))
        return {asset: digest}, conflicts

    # ------------------------------------------------------------ helpers
    @staticmethod
    def _write_json(path: Path, data: Any) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_name(f".{path.name}.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(tmp, path)


def _label(session: str) -> str:
    return "la campagne" if session == CAMPAIGN else f"« {session} »"
