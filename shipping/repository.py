import json
import sqlite3
import uuid
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime, timezone
from .ingestion import digest


def now():
    return datetime.now(timezone.utc).isoformat()


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), default=str)


class RevisionConflict(ValueError):
    pass


class Repository:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        for folder in ('uploads', 'exports'):
            (self.root / folder).mkdir(exist_ok=True)
        self.db = self.root / 'shipping.sqlite3'
        with self.connect() as c:
            c.executescript('''
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS schema_version(version INTEGER PRIMARY KEY);
            INSERT OR IGNORE INTO schema_version VALUES(1);
            CREATE TABLE IF NOT EXISTS shipments(id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS shipment_revisions(shipment_id TEXT REFERENCES shipments(id),
              revision INTEGER, payload TEXT NOT NULL, created_at TEXT, PRIMARY KEY(shipment_id, revision));
            CREATE TABLE IF NOT EXISTS files(id TEXT PRIMARY KEY, name TEXT, sha256 TEXT UNIQUE,
              path TEXT, role TEXT, scope TEXT, status TEXT, created_at TEXT);
            CREATE TABLE IF NOT EXISTS file_links(file_id TEXT REFERENCES files(id), shipment_id TEXT REFERENCES shipments(id),
              role TEXT, scope TEXT, PRIMARY KEY(file_id, shipment_id, role, scope));
            CREATE TABLE IF NOT EXISTS records(kind TEXT, id TEXT, shipment_id TEXT, payload TEXT, created_at TEXT,
              PRIMARY KEY(kind,id));
            CREATE TABLE IF NOT EXISTS extraction_cache(key TEXT PRIMARY KEY, payload TEXT, created_at TEXT);
            ''')
            for kind in ('observations', 'plans', 'documents', 'issues'):
                c.execute(f"CREATE VIEW IF NOT EXISTS {kind} AS SELECT * FROM records WHERE kind='{kind}'")

    @contextmanager
    def connect(self):
        c = sqlite3.connect(self.db, timeout=30)
        c.row_factory = sqlite3.Row
        c.execute('PRAGMA foreign_keys=ON')
        try:
            with c:
                yield c
        finally:
            c.close()

    def save(self, sid, payload, expected_revision):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row = c.execute('SELECT revision FROM shipments WHERE id=?', (sid,)).fetchone()
            previous = row['revision'] if row else 0
            if previous != expected_revision:
                raise RevisionConflict('VERSION_CONFLICT: 请重新载入当前版本')
            if previous:
                old = json.loads(c.execute('SELECT payload FROM shipment_revisions WHERE shipment_id=? AND revision=?', (sid, previous)).fetchone()[0])
                comparable = dict(payload)
                comparable['revision'] = previous
                if canonical(old) == canonical(comparable):
                    return previous
            revision = previous + 1
            saved = dict(payload, revision=revision)
            c.execute('INSERT INTO shipments VALUES(?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision', (sid, revision))
            c.execute('INSERT INTO shipment_revisions VALUES(?,?,?,?)', (sid, revision, canonical(saved), now()))
            return revision

    def load(self, sid, revision=None):
        with self.connect() as c:
            if revision is None:
                row = c.execute('SELECT revision FROM shipments WHERE id=?', (sid,)).fetchone()
                if not row:
                    raise KeyError(sid)
                revision = row[0]
            row = c.execute('SELECT payload FROM shipment_revisions WHERE shipment_id=? AND revision=?', (sid, revision)).fetchone()
            if not row:
                raise KeyError((sid, revision))
            return json.loads(row[0])

    def list_shipments(self):
        with self.connect() as c:
            return [dict(row) for row in c.execute('SELECT * FROM shipments ORDER BY rowid DESC')]

    def put(self, kind, sid, value, rid=None):
        rid = rid or str(uuid.uuid4())
        with self.connect() as c:
            c.execute('INSERT INTO records VALUES(?,?,?,?,?) ON CONFLICT(kind,id) DO UPDATE SET payload=excluded.payload',
                      (kind, rid, sid, canonical(value), now()))
        return rid

    def records(self, kind, sid=None):
        with self.connect() as c:
            rows = c.execute('SELECT id,payload FROM records WHERE kind=?' + (' AND shipment_id=?' if sid else '') + ' ORDER BY rowid',
                             (kind, sid) if sid else (kind,))
            return [dict(json.loads(r['payload']), id=r['id']) for r in rows]

    def put_file(self, name, data, role='unclassified', scope='', status='IMPORTED', sid=None):
        sha = digest(data)
        path = self.root / 'uploads' / sha
        if not path.exists():
            path.write_bytes(data)
            path.chmod(0o444)
        with self.connect() as c:
            c.execute('INSERT OR IGNORE INTO files VALUES(?,?,?,?,?,?,?,?)',
                      (sha, name, sha, str(path.relative_to(self.root)), role, scope, status, now())[:8])
            if sid:
                c.execute('INSERT OR IGNORE INTO file_links VALUES(?,?,?,?)', (sha, sid, role, scope))
            row = c.execute('SELECT * FROM files WHERE id=?', (sha,)).fetchone()
        return dict(row)

    def files(self, sid=None):
        with self.connect() as c:
            query = ('SELECT f.*, l.role AS linked_role,l.scope AS linked_scope FROM files f JOIN file_links l ON f.id=l.file_id WHERE l.shipment_id=?'
                     if sid else 'SELECT * FROM files')
            return [dict(x) for x in c.execute(query, (sid,) if sid else ())]

    def file_bytes(self, file_id):
        with self.connect() as c:
            row = c.execute('SELECT path,sha256 FROM files WHERE id=?', (file_id,)).fetchone()
        if not row:
            raise KeyError(file_id)
        data = self.safe_path(row['path']).read_bytes()
        if digest(data) != row['sha256']:
            raise ValueError('FILE_HASH_MISMATCH')
        return data

    def safe_path(self, relative):
        path = (self.root / relative).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError('PATH_OUTSIDE_DATA')
        return path

    def cache_get(self, key):
        with self.connect() as c:
            row = c.execute('SELECT payload FROM extraction_cache WHERE key=?', (key,)).fetchone()
            return json.loads(row[0]) if row else None

    def cache_set(self, key, value):
        with self.connect() as c:
            c.execute('INSERT OR REPLACE INTO extraction_cache VALUES(?,?,?)', (key, canonical(value), now()))
