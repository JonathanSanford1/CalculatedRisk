"""A small in-memory stand-in for Firestore, covering only what the backend
uses, so the tests run with no network and no emulator."""

import copy
import threading
from datetime import datetime, timezone

from google.api_core.exceptions import AlreadyExists


def _is_increment(value):
    return type(value).__name__ == "Increment"


def _is_server_timestamp(value):
    return type(value).__name__ == "Sentinel"


def _resolve(existing, data, merge):
    """The document that results from writing data over existing."""
    result = copy.deepcopy(existing) if merge and isinstance(existing, dict) else {}
    for key, value in data.items():
        if _is_server_timestamp(value):
            result[key] = datetime.now(timezone.utc)
        elif _is_increment(value):
            result[key] = (result.get(key) or 0) + value.value
        elif isinstance(value, dict):
            result[key] = _resolve(result.get(key), value, True)
        else:
            result[key] = copy.deepcopy(value)
    return result


class FakeDB:
    def __init__(self):
        self.docs: dict[tuple, dict] = {}
        self.log: list[tuple[str, tuple]] = []  # ("set" | "create" | "delete", path)
        self.lock = threading.RLock()

    def collection(self, name):
        return FakeCollection(self, (name,))

    def collection_group(self, name):
        return FakeGroup(self, name)

    def batch(self):
        return FakeBatch(self)

    def writes_to(self, *path):
        return [op for op, p in self.log if p == tuple(path)]

    def dump(self, prefix=()):
        with self.lock:
            return {"/".join(p): copy.deepcopy(d) for p, d in sorted(self.docs.items())
                    if p[:len(prefix)] == tuple(prefix)}


class FakeSnapshot:
    def __init__(self, db, path, data):
        self._db, self._path, self._data = db, path, data
        self.id = path[-1]
        self.exists = data is not None
        self.reference = FakeDocRef(db, path)

    def to_dict(self):
        return copy.deepcopy(self._data) if self._data is not None else None


class FakeDocRef:
    def __init__(self, db, path):
        self._db, self._path = db, path
        self.id = path[-1]

    @property
    def parent(self):
        return FakeCollection(self._db, self._path[:-1])

    def collection(self, name):
        return FakeCollection(self._db, self._path + (name,))

    def get(self):
        with self._db.lock:
            data = self._db.docs.get(self._path)
            return FakeSnapshot(self._db, self._path, copy.deepcopy(data) if data is not None else None)

    def set(self, data, merge=False):
        with self._db.lock:
            self._db.docs[self._path] = _resolve(self._db.docs.get(self._path), data, merge)
            self._db.log.append(("set", self._path))

    def create(self, data):
        with self._db.lock:
            if self._path in self._db.docs:
                raise AlreadyExists("document already exists")
            self._db.docs[self._path] = _resolve(None, data, False)
            self._db.log.append(("create", self._path))

    def delete(self):
        with self._db.lock:
            self._db.docs.pop(self._path, None)
            self._db.log.append(("delete", self._path))


class FakeCollection:
    def __init__(self, db, path):
        self._db, self._path = db, path

    @property
    def parent(self):
        return FakeDocRef(self._db, self._path[:-1]) if len(self._path) > 1 else None

    def document(self, doc_id):
        return FakeDocRef(self._db, self._path + (doc_id,))

    def stream(self):
        with self._db.lock:
            n = len(self._path)
            return [FakeSnapshot(self._db, p, copy.deepcopy(d))
                    for p, d in sorted(self._db.docs.items())
                    if len(p) == n + 1 and p[:n] == self._path]


class FakeGroup:
    def __init__(self, db, name):
        self._db, self._name = db, name

    def stream(self):
        with self._db.lock:
            return [FakeSnapshot(self._db, p, copy.deepcopy(d))
                    for p, d in sorted(self._db.docs.items())
                    if len(p) % 2 == 0 and p[-2] == self._name]


class FakeBatch:
    def __init__(self, db):
        self._db, self._ops = db, []

    def set(self, ref, data, merge=False):
        self._ops.append(lambda: ref.set(data, merge))

    def delete(self, ref):
        self._ops.append(ref.delete)

    def commit(self):
        for op in self._ops:
            op()
        self._ops = []
