"""Loopback Qdrant vector reuse for already-authorized candidate text.

No curriculum/owner lookup is performed here. Evidence admission and citations
remain in PersonalEvidenceService. The cache stores vectors, not student data.
"""
from hashlib import sha256
import json
import math
import os
import threading
import urllib.request
import urllib.error
from urllib.parse import urlparse
import uuid

from .semantic_retrieval import LocalSemanticRetriever, _Unavailable, DIMENSION, QUERY_PREFIX, MAX_TEXT_CHARS


def collection_name(identity):
    return 'curriculum_embeddings_' + identity[:24]


def text_key(identity, text):
    # Same content key as the corpus ingestion SQLite vector cache.
    return sha256(json.dumps([identity, text], ensure_ascii=False, sort_keys=True).encode()).hexdigest()


def point_id(key):
    return str(uuid.uuid5(uuid.NAMESPACE_URL, key))


class QdrantCache:
    def __init__(self, url):
        parsed = urlparse(url)
        if (parsed.scheme != 'http' or parsed.hostname not in {'127.0.0.1', 'localhost'}
                or parsed.username or parsed.password or parsed.path not in {'','/'} or parsed.query or parsed.fragment):
            raise ValueError('qdrant_loopback_required')
        self.url = url.rstrip('/')
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

    def request(self, method, path, body=None):
        data = json.dumps(body, allow_nan=False).encode() if body is not None else None
        request = urllib.request.Request(self.url+path, data=data, method=method,
                                         headers={'Content-Type':'application/json'})
        try:
            with self.opener.open(request, timeout=10) as response:
                raw = response.read(24*1024*1024+1)
            if len(raw)>24*1024*1024:raise ValueError('oversized_response')
            return json.loads(raw)['result']
        except (OSError, ValueError, KeyError) as error:
            raise _Unavailable('vector_store_unavailable') from None

    def ensure(self, collection, *, create=True):
        # Concurrent first callers may both observe absence. Creation is idempotent.
        collections = self.request('GET','/collections')['collections']
        if any(r['name']==collection for r in collections):return
        if not create:raise _Unavailable('vector_store_not_initialized')
        try:
            self.request('PUT','/collections/'+collection,
                         {'vectors':{'size':DIMENSION,'distance':'Cosine','on_disk':True},'on_disk_payload':True})
        except _Unavailable:
            if not any(r['name']==collection for r in self.request('GET','/collections')['collections']):raise

    def retrieve(self, collection, keys):
        found={}
        for start in range(0,len(keys),128):
            ids={point_id(k):k for k in keys[start:start+128]}
            rows=self.request('POST','/collections/'+collection+'/points',
                              {'ids':list(ids),'with_vector':True,'with_payload':True})
            for r in rows:
                v=r['vector'];payload=r.get('payload') or {}
                if (r['id'] not in ids or payload.get('text_key')!=ids[r['id']] or len(v)!=DIMENSION
                        or any(type(x) not in (int,float) or not math.isfinite(x) for x in v)
                        or abs(sum(x*x for x in v)-1)>0.02):raise _Unavailable('vector_cache_invalid')
                found[ids[r['id']]]=(v,bool(payload.get('truncated')))
        return found

    def put(self, collection, rows):
        for start in range(0,len(rows),128):
            self.request('PUT','/collections/'+collection+'/points?wait=true',{'points':[
                {'id':point_id(key),'vector':vector,'payload':{'text_key':key,'truncated':truncated}}
                for key,vector,truncated in rows[start:start+128]]})


class PersistentSemanticRetriever(LocalSemanticRetriever):
    def __init__(self, manifest_path, *, python_path=None, url, timeout_seconds=45):
        super().__init__(manifest_path,python_path=python_path,timeout_seconds=timeout_seconds)
        self.cache=QdrantCache(url)
        self._operation_lock=threading.RLock()
        self._new_vectors=0
        self._cache_hits=0
        self.allow_write=False
        self._authorize_write=None

    def _run_encoder(self, texts, *, expected_identity=None, authorize=None):
        status=expected_identity or self.status()
        collection=collection_name(status['manifest_sha256'])
        self.cache.ensure(collection, create=self.allow_write)
        keys=[text_key(status['manifest_sha256'],t) for t in texts[1:]]
        existing=self.cache.retrieve(collection,list(dict.fromkeys(keys)))
        missing={k:t for k,t in zip(keys,texts[1:]) if k not in existing}
        output=super()._run_encoder([texts[0],*missing.values()],expected_identity=status)
        truncated=set(output.get('token_truncated_indices',[]))
        rows=[]
        for i,key in enumerate(missing,1):
            existing[key]=(output['vectors'][i],i in truncated)
            rows.append((key,output['vectors'][i],i in truncated))
        if self.allow_write and rows:
            guard=authorize or self._authorize_write
            if guard is None:raise _Unavailable('vector_publication_requires_authorization')
            guard()  # A session may have been revoked while the encoder ran.
            self.cache.put(collection,rows)
        self._new_vectors+=len(missing)
        self._cache_hits+=len(keys)-len(missing)
        indices=([0] if 0 in truncated else [])+[i for i,k in enumerate(keys,1) if existing[k][1]]
        return {**output,'vectors':[output['vectors'][0]]+[existing[k][0] for k in keys],
                'token_truncated_indices':indices,'token_truncated_count':len(indices)}

    def rank(self, query, candidates, limit=5):
        with self._operation_lock:
            self._new_vectors=self._cache_hits=0
            result=super().rank(query,candidates,limit)
            result.update(encoded_count=self._new_vectors,persistent_cache_hits=self._cache_hits,
                          vector_backend='qdrant_server')
            return result

    def rank_authorized(self, query, candidates, limit, authorize):
        with self._operation_lock:
            self._authorize_write=authorize
            try:
                authorize()
                result=self.rank(query,candidates,limit)
                authorize()
                return result
            finally:self._authorize_write=None

    def warm(self, texts, authorize):
        with self._operation_lock:
            status=self.status()
            if status['status']!='available':raise _Unavailable(status['reason'])
            self._new_vectors=self._cache_hits=0
            missing_count=0
            unique=list(dict.fromkeys(t[:MAX_TEXT_CHARS] for t in texts if t.strip()))
            collection=collection_name(status['manifest_sha256']);self.cache.ensure(collection, create=self.allow_write)
            # Reused documents need no query embedding either.
            for start in range(0,len(unique),64):
                authorize()
                batch=unique[start:start+64]
                keys=[text_key(status['manifest_sha256'],t) for t in batch]
                found=self.cache.retrieve(collection,keys)
                missing=[t for k,t in zip(keys,batch) if k not in found]
                self._cache_hits+=len(found)
                missing_count+=len(missing)
                if missing and self.allow_write:self._run_encoder([QUERY_PREFIX+'培养资料',*missing],expected_identity=status,authorize=authorize)
                authorize()
            return {'status':'ready' if self.allow_write or not missing_count else 'existing_corpus_missing_vectors',
                    'missing_vectors':missing_count if not self.allow_write else 0,
                    'write_allowed':self.allow_write,'vectors':len(unique),'encoded_count':self._new_vectors,
                    'persistent_cache_hits':self._cache_hits,'vector_backend':'qdrant_server'}
