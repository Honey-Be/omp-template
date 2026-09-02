import sqlite3
import os
import struct
import faulthandler
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

# C/C++ 네이티브 확장에서 발생하는 치명적 충돌(Segfault 등) 시 파이썬 콜스택을 강제로 출력하도록 활성화합니다.
faulthandler.enable()

try:
    import sqlite_vec
except ImportError:
    raise RuntimeError("sqlite-vec 패키지가 설치되지 않았습니다.")

try:
    from sentence_transformers import SentenceTransformer
except ImportError:
    raise RuntimeError("sentence-transformers 패키지가 설치되지 않았습니다.")

MODEL_NAME = 'paraphrase-multilingual-MiniLM-L12-v2'
_model_instance: Optional[SentenceTransformer] = None

app = FastAPI(title="Oh-My-Pi Context Memory Daemon")
DB_PATH = os.environ.get("DB_PATH", "/app/data/context.db")

class SaveRequest(BaseModel):
    session_id: str
    role: str
    content: str
    tags: str

class SearchRequest(BaseModel):
    query: str
    limit: int = 5

class MemoryRecord(BaseModel):
    session_id: str
    timestamp: str
    role: str
    content: str
    tags: str

class ImportRequest(BaseModel):
    records: List[MemoryRecord]

class DeleteRequest(BaseModel):
    ids: Optional[List[int]] = None
    session_id: Optional[str] = None

def get_db_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    return conn

def get_model() -> SentenceTransformer:
    global _model_instance
    if _model_instance is None:
        print(f"[{datetime.now().isoformat()}] 임베딩 모델 로딩을 시작합니다: {MODEL_NAME}")
        _model_instance = SentenceTransformer(MODEL_NAME)
        print(f"[{datetime.now().isoformat()}] 임베딩 모델 로딩 완료.")
    return _model_instance

@app.on_event("startup")
def init_db():
    conn = get_db_connection()
    cursor = conn.cursor()

    cursor.execute('''
        CREATE TABLE IF NOT EXISTS memory (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT,
            timestamp TEXT,
            role TEXT,
            content TEXT,
            tags TEXT
        )
    ''')

    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_fts USING fts5(
            content, tags, content='memory', content_rowid='id'
        )
    ''')

    cursor.execute('''
        CREATE VIRTUAL TABLE IF NOT EXISTS memory_vec USING vec0(
            embedding float[384]
        )
    ''')

    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS memory_ai AFTER INSERT ON memory BEGIN
            INSERT INTO memory_fts(rowid, content, tags)
            VALUES (new.id, new.content, new.tags);
        END;
    ''')

    # memory_fts 는 외부 콘텐츠(content='memory') FTS5 테이블이므로, 행이
    # 삭제될 때 FTS5 의 특수 'delete' 명령으로 인덱스를 함께 되돌려야 합니다.
    # 이때 넘기는 값은 INSERT 시 색인한 것과 동일한 컬럼(old.content, old.tags)
    # 이어야 하며, 그렇지 않으면 인덱스가 어긋난 채로 남습니다.
    cursor.execute('''
        CREATE TRIGGER IF NOT EXISTS memory_ad AFTER DELETE ON memory BEGIN
            INSERT INTO memory_fts(memory_fts, rowid, content, tags)
            VALUES ('delete', old.id, old.content, old.tags);
        END;
    ''')

    conn.commit()
    conn.close()
    print(f"[{datetime.now().isoformat()}] 데이터베이스 스키마 검증 완료.")

@app.post("/save")
def save_memory(req: SaveRequest):
    try:
        model = get_model()
        conn = get_db_connection()
        cursor = conn.cursor()

        cursor.execute('''
            INSERT INTO memory (session_id, timestamp, role, content, tags)
            VALUES (?, ?, ?, ?, ?)
        ''', (req.session_id, datetime.now().isoformat(), req.role, req.content, req.tags))

        row_id = cursor.lastrowid

        text_to_embed = f"{req.tags} {req.content}"
        embedding = model.encode(text_to_embed).tolist()
        embedding_bytes = struct.pack(f"{len(embedding)}f", *embedding)

        cursor.execute('''
            INSERT INTO memory_vec (rowid, embedding)
            VALUES (?, ?)
        ''', (row_id, embedding_bytes))

        conn.commit()
        conn.close()
        return {"status": "success", "id": row_id}
    except Exception as e:
        print(f"Save 오류 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

def build_fts_match_query(raw: str) -> Optional[str]:
    """
    자연어 질의를 FTS5 MATCH 식으로 안전하게 변환합니다.

    FTS5 는 쉼표/괄호/하이픈/콜론/따옴표와 AND OR NOT NEAR 를 질의 문법으로
    해석하므로, 자연어를 그대로 MATCH 에 넘기면
    "fts5: syntax error near ..." 로 실패합니다. 각 토큰을 FTS5 문자열
    리터럴(이중 따옴표)로 감싸면 내용이 전부 리터럴로 취급됩니다.

    토큰은 OR 로 결합합니다. FTS5 의 기본 결합은 암묵적 AND 인데, 자연어
    질의는 토큰 수가 많아 AND 로는 사실상 매칭되지 않아 어휘 검색 축이
    죽습니다. OR 로 재현율을 확보하고 최종 순위는 RRF 융합이 정하도록
    둡니다.

    매칭할 토큰이 하나도 없으면 None 을 반환합니다(어휘 검색 생략).
    """
    tokens = re.findall(r"\w+", raw)
    if not tokens:
        return None
    return " OR ".join('"' + token.replace('"', '""') + '"' for token in tokens)

@app.post("/search")
def search_memory(req: SearchRequest):
    try:
        model = get_model()
        conn = get_db_connection()
        cursor = conn.cursor()
        RRF_K = 60

        match_query = build_fts_match_query(req.query)
        fts_results: List[Any] = []
        if match_query:
            try:
                cursor.execute('''
                    SELECT rowid, rank
                    FROM memory_fts
                    WHERE memory_fts MATCH ?
                    LIMIT 20
                ''', (match_query,))
                fts_results = cursor.fetchall()
            except sqlite3.OperationalError as e:
                # 어휘 검색 실패는 치명적이지 않습니다. 벡터 검색만으로도
                # 결과를 만들 수 있으므로 질의 전체를 500 으로 실패시키지
                # 않고, 기록만 남기고 벡터 전용으로 계속 진행합니다.
                print(f"Search FTS 경로 실패(벡터 전용으로 계속): {str(e)}")
                fts_results = []

        query_embedding = model.encode(req.query).tolist()
        query_bytes = struct.pack(f"{len(query_embedding)}f", *query_embedding)

        cursor.execute('''
            SELECT rowid, distance
            FROM memory_vec
            WHERE embedding MATCH ? AND k = 20
        ''', (query_bytes,))
        vec_results = cursor.fetchall()

        scores: Dict[int, float] = {}
        for rank_idx, row in enumerate(fts_results):
            row_id = row['rowid']
            scores[row_id] = scores.get(row_id, 0.0) + (1.0 / (RRF_K + rank_idx + 1))

        for rank_idx, row in enumerate(vec_results):
            row_id = row['rowid']
            scores[row_id] = scores.get(row_id, 0.0) + (1.0 / (RRF_K + rank_idx + 1))

        ranked_row_ids = sorted(scores.keys(), key=lambda x: scores[x], reverse=True)[:req.limit]

        results = []
        if ranked_row_ids:
            placeholders = ','.join('?' for _ in ranked_row_ids)
            cursor.execute(f'''
                SELECT id, session_id, timestamp, role, content, tags
                FROM memory
                WHERE id IN ({placeholders})
            ''', ranked_row_ids)

            rows_by_id = {row['id']: dict(row) for row in cursor.fetchall()}
            for row_id in ranked_row_ids:
                if row_id in rows_by_id:
                    results.append(rows_by_id[row_id])

        conn.close()
        return {"results": results}
    except Exception as e:
        print(f"Search 오류 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/export")
def export_memory():
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT session_id, timestamp, role, content, tags FROM memory")
        rows = cursor.fetchall()
        conn.close()
        return {"status": "success", "records": [dict(row) for row in rows]}
    except Exception as e:
        print(f"Export 오류 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/import")
def import_memory(req: ImportRequest):
    try:
        model = get_model()
        conn = get_db_connection()
        cursor = conn.cursor()
        inserted_count = 0

        for record in req.records:
            cursor.execute('''
                INSERT INTO memory (session_id, timestamp, role, content, tags)
                VALUES (?, ?, ?, ?, ?)
            ''', (record.session_id, record.timestamp, record.role, record.content, record.tags))
            row_id = cursor.lastrowid

            text_to_embed = f"{record.tags} {record.content}"
            embedding = model.encode(text_to_embed).tolist()
            embedding_bytes = struct.pack(f"{len(embedding)}f", *embedding)

            cursor.execute('''
                INSERT INTO memory_vec (rowid, embedding)
                VALUES (?, ?)
            ''', (row_id, embedding_bytes))
            inserted_count += 1

        conn.commit()
        conn.close()
        return {"status": "success", "inserted_count": inserted_count}
    except Exception as e:
        print(f"Import 오류 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/delete")
def delete_memory(req: DeleteRequest):
    # 조건 없는 전체 삭제를 막습니다. 최소 한 개의 필터가 필요합니다.
    if not req.ids and not req.session_id:
        raise HTTPException(
            status_code=400,
            detail="삭제 조건이 없습니다. 'ids' 또는 'session_id' 중 하나 이상을 지정해야 합니다."
        )

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 필터는 AND 로 결합합니다. session_id 는 바인딩 파라미터로만 들어가므로
        # f-string 에 삽입되는 것은 자리표시자(?)와 컬럼명 리터럴뿐입니다.
        conditions: List[str] = []
        params: List[Any] = []
        if req.ids:
            id_filter_placeholders = ','.join('?' for _ in req.ids)
            conditions.append(f"id IN ({id_filter_placeholders})")
            params.extend(req.ids)
        if req.session_id:
            conditions.append("session_id = ?")
            params.append(req.session_id)
        where_clause = ' AND '.join(conditions)

        # 대상 id 를 먼저 확정합니다. memory_vec 은 트리거가 아니라 아래에서
        # 명시적으로 정리하므로, 삭제 전에 rowid 목록이 필요합니다.
        cursor.execute(f"SELECT id FROM memory WHERE {where_clause}", params)
        target_ids = [row['id'] for row in cursor.fetchall()]

        if not target_ids:
            conn.close()
            return {"status": "success", "deleted_count": 0, "deleted_ids": []}

        id_placeholders = ','.join('?' for _ in target_ids)

        # vec0 가상 테이블은 INSERT 경로(save/import)와 동일하게 파이썬에서
        # 명시적으로 정리합니다. 트리거에 넣지 않는 이유는, 확장(sqlite-vec)이
        # 로드되지 않은 연결에서 memory 를 삭제하면 트리거가 실패해 삭제 자체가
        # 막히기 때문입니다.
        cursor.execute(
            f"DELETE FROM memory_vec WHERE rowid IN ({id_placeholders})",
            target_ids
        )

        # 이 DELETE 가 memory_ad 트리거를 발화시켜 FTS5 인덱스를 되돌립니다.
        cursor.execute(
            f"DELETE FROM memory WHERE id IN ({id_placeholders})",
            target_ids
        )

        conn.commit()
        conn.close()
        return {
            "status": "success",
            "deleted_count": len(target_ids),
            "deleted_ids": target_ids
        }
    except Exception as e:
        print(f"Delete 오류 발생: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))
