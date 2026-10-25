"""Copy a local curriculum Qdrant and its vector cache to a loopback server.

Idempotent upserts, no OCR or embedding, original database is retained.
"""
import argparse
import json
from pathlib import Path
import sqlite3
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from qdrant_client import QdrantClient, models
from course_assistant.persistent_semantics import QdrantCache,collection_name,point_id


def main():
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--root',required=True);p.add_argument('--url',default='http://127.0.0.1:6333')
    a=p.parse_args();root=Path(a.root);QdrantCache(a.url)
    report=json.loads((root/'last_ingest.json').read_text());collection=report['collection']
    source=QdrantClient(path=str(root/'qdrant'));target=QdrantClient(url=a.url,timeout=60)
    try:
        if not target.collection_exists(collection):
            target.create_collection(collection,vectors_config=models.VectorParams(size=512,distance=models.Distance.COSINE,on_disk=True),
                on_disk_payload=True,hnsw_config=models.HnswConfigDiff(on_disk=True,max_indexing_threads=2),
                optimizers_config=models.OptimizersConfigDiff(indexing_threshold=0))
        for field,kind in [('document_id','keyword'),('pdf_sha256','keyword'),('cohort','integer'),('plan_number','keyword')]:
            target.create_payload_index(collection,field_name=field,field_schema=kind,wait=True)
        count=0;offset=None
        while True:
            rows,offset=source.scroll(collection,offset=offset,limit=256,with_payload=True,with_vectors=True)
            if rows:target.upsert(collection,[models.PointStruct(id=r.id,vector=r.vector,payload=r.payload) for r in rows],wait=True)
            count+=len(rows)
            if count%4096==0:print(json.dumps({'copied_points':count}),flush=True)
            if offset is None:break
        target.update_collection(collection,optimizers_config=models.OptimizersConfigDiff(indexing_threshold=10000))
        embeddings=collection_name(report['encoder_identity']);QdrantCache(a.url).ensure(embeddings)
        db=sqlite3.connect(f'file:{root / "catalogue.sqlite3"}?mode=ro',uri=True)
        cached=0
        try:
            cursor=db.execute('SELECT id,vector FROM vectors')
            while batch:=cursor.fetchmany(256):
                target.upsert(embeddings,[models.PointStruct(id=point_id(key),vector=json.loads(vector),payload={'text_key':key,'truncated':False}) for key,vector in batch],wait=True)
                cached+=len(batch)
        finally:db.close()
        actual=target.count(collection,exact=True).count
        cache_count=target.count(embeddings,exact=True).count
        assert actual>=count==report['persisted_points'] and cache_count>=cached
        result={'status':'migrated','collection':collection,'points':count,'embedding_cache_points':cached,
                'source_retained':True,'new_encodings':0,'url':a.url}
        (root/'server_migration.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result),flush=True)
    finally:source.close();target.close()

if __name__=='__main__':main()
