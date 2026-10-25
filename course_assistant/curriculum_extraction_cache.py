"""Exact-version, owner-scoped extraction cache; not a searchable corpus."""
from hashlib import sha256
import json
import os
from pathlib import Path
import sqlite3
import stat


class ExtractionCache:
    def __init__(self, corpus_path):
        self.path=Path(corpus_path).resolve().parent/'runtime_extractions.sqlite3'

    def _open(self, *, create=False):
        if create and not self.path.exists():
            try:
                fd=os.open(self.path,os.O_CREAT|os.O_EXCL|os.O_WRONLY|getattr(os,'O_NOFOLLOW',0),0o600)
                os.close(fd)
            except FileExistsError:pass
        if not self.path.exists():return None
        if self.path.is_symlink() or stat.S_IMODE(self.path.stat().st_mode)!=0o600:
            raise ValueError('invalid_extraction_cache')
        return sqlite3.connect(self.path.as_uri()+('?mode=rw' if create else '?mode=ro'),uri=True,timeout=5)

    def read(self, key):
        db=self._open()
        if db is None:return None
        try:
            row=db.execute('SELECT bundle,digest FROM extractions WHERE cache_key=?',(key,)).fetchone()
            if row is None:return None
            if sha256(row[0].encode()).hexdigest()!=row[1]:raise ValueError('extraction_cache_corrupt')
            return json.loads(row[0])
        finally:db.close()

    def write(self, key, bundle, authorize):
        raw=json.dumps(bundle,ensure_ascii=False,sort_keys=True)
        if len(raw.encode())>16*1024*1024:raise ValueError('extraction_cache_limit')
        authorize()
        db=self._open(create=True)
        try:
            db.execute('CREATE TABLE IF NOT EXISTS extractions(cache_key TEXT PRIMARY KEY,bundle TEXT NOT NULL,digest TEXT NOT NULL)')
            authorize()
            db.execute('INSERT OR REPLACE INTO extractions VALUES(?,?,?)',(key,raw,sha256(raw.encode()).hexdigest()))
            authorize()
            db.commit()
        finally:db.close()
