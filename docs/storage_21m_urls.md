# Storage strategy dla 21M URL + embeddingi

## Skala problemu

- **21M URL × ~10 KB JSON** ≈ **200 GB** samych wyników
- **Encji łącznie:** 21M × 22 (median) ≈ **460M encji**
- **Embeddingi:**
  - per article (uśredniony pool): 21M × 768 dim × FP16 = **~30 GB**
  - per entity: 460M × 768 × FP16 = **~700 GB**
  - z FP8 quantyzacją: **~350 GB**
- **Indexy** (HNSW dla vector search): +20-30% overhead

## Wymagania

| Cel | Operacja | Częstotliwość |
|---|---|---|
| ETL append | INSERT batch (Step 1 + Step 2 output) | High (21M URL × 2 stepy) |
| Lookup by url_hash | SELECT WHERE url_hash = ? | Medium (idempotency check) |
| Filter by category | SELECT WHERE category IN (...) | Medium (analytics) |
| Filter by entity name | LEFT JOIN entities WHERE name = ? | Medium (entity discovery) |
| Vector similarity | k-NN by embedding | High (semantic search) |
| Aggregation | COUNT GROUP BY type/category | Low (reports) |

## Opcje (od najprostszej)

### A. SQLite + sqlite-vec (research/dev)

**Stos:**
- SQLite 3.45+ (WAL mode)
- [`sqlite-vec`](https://github.com/asg017/sqlite-vec) — vector search extension (HNSW + brute force)
- Python: `sqlite3` standard library + `sqlite-vec` Python package

**Plusy:**
- ✅ Single file (~200 GB), łatwy backup (`cp` lub `litestream`)
- ✅ Zero setup — wystarczy `pip install sqlite-vec`
- ✅ Wystarcza dla 21M wierszy (testowane do **280 TB** technicznie)
- ✅ Pełen SQL (GROUP BY, JOIN, subqueries) — wszystko czego potrzebujesz
- ✅ WAL mode pozwala na concurrent reads + 1 writer
- ✅ Vector search via `sqlite-vec` MATCH operator

**Minusy:**
- ⚠️ Single-writer (wszystkie INSERT przez 1 process)
- ⚠️ Plik 200+ GB to UX challenge (kopiowanie, replikacja)
- ⚠️ Nie ma natywnej replikacji (potrzebujesz `litestream` dla S3 backup)

**Schema:**

```sql
CREATE TABLE articles (
  url_hash TEXT PRIMARY KEY,
  url TEXT, domain TEXT, path TEXT,
  category TEXT,
  language TEXT,
  title TEXT, meta_description TEXT, h1 TEXT, article_summary TEXT,
  text_tokens INTEGER,
  step1_latency_s REAL, step2_latency_s REAL,
  created_at INTEGER
);

CREATE TABLE entities (
  url_hash TEXT,
  position INTEGER,
  name TEXT,
  type TEXT,
  category TEXT,    -- Azure high-level
  strength TEXT,    -- strong/weak
  metadata JSON,
  PRIMARY KEY (url_hash, position),
  FOREIGN KEY (url_hash) REFERENCES articles(url_hash)
);

CREATE INDEX idx_entities_url ON entities(url_hash);
CREATE INDEX idx_entities_name ON entities(name);
CREATE INDEX idx_entities_type ON entities(type);
CREATE INDEX idx_articles_category ON articles(category);
CREATE INDEX idx_articles_domain ON articles(domain);

-- vector store (sqlite-vec)
CREATE VIRTUAL TABLE article_embeddings USING vec0(
  url_hash TEXT PRIMARY KEY,
  embedding FLOAT[768]
);

CREATE VIRTUAL TABLE entity_embeddings USING vec0(
  entity_id TEXT PRIMARY KEY,    -- url_hash + position
  embedding FLOAT[768]
);
```

**Kiedy wybrać:** dev/staging, max 1 query worker, lokalny prototyp.

### B. PostgreSQL + pgvector (production standard)

**Stos:**
- PostgreSQL 16+
- [`pgvector`](https://github.com/pgvector/pgvector) — vector search extension (HNSW + IVFFlat)
- Optionally: `pg_trgm` (fuzzy text search), `tsvector` (full-text)

**Plusy:**
- ✅ ACID, replikacja, monitoring (Prometheus exporters)
- ✅ Horizontal scaling via Citus jeśli kiedyś przekroczymy 100M
- ✅ Bogate query (CTE, window functions, JSON ops)
- ✅ pgvector HNSW: ~1ms k-NN dla 21M wektorów
- ✅ Ekosystem (pgAdmin, Supabase, AWS RDS, Cloud SQL)
- ✅ Concurrent writes/reads bez problemów
- ✅ JSONB dla metadata — indexable

**Minusy:**
- ⚠️ Setup (Docker / managed RDS) i konfiguracja (work_mem, shared_buffers, autovacuum)
- ⚠️ Backup/replikacja to praca DevOps
- ⚠️ Koszt managed (~$50-200/m dla 21M na małym RDS)

**Schema (pgvector):**

```sql
CREATE EXTENSION vector;
CREATE EXTENSION pg_trgm;

CREATE TABLE articles (
  url_hash CHAR(64) PRIMARY KEY,
  url TEXT NOT NULL,
  domain TEXT,
  path TEXT,
  category TEXT,
  language CHAR(2),
  title TEXT, meta_description TEXT, h1 TEXT, article_summary TEXT,
  text_tokens INT,
  step1_latency_s REAL, step2_latency_s REAL,
  embedding vector(768),
  created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE TABLE entities (
  id BIGSERIAL PRIMARY KEY,
  url_hash CHAR(64) REFERENCES articles(url_hash) ON DELETE CASCADE,
  position INT,
  name TEXT NOT NULL,
  type TEXT NOT NULL,
  category TEXT,
  strength TEXT,
  metadata JSONB,
  embedding vector(768)
);

CREATE INDEX articles_emb_hnsw ON articles USING hnsw (embedding vector_cosine_ops);
CREATE INDEX entities_emb_hnsw ON entities USING hnsw (embedding vector_cosine_ops);
CREATE INDEX entities_url ON entities(url_hash);
CREATE INDEX entities_name_trgm ON entities USING gin (name gin_trgm_ops);
CREATE INDEX entities_type_cat ON entities(type, category);
CREATE INDEX articles_category ON articles(category);
CREATE INDEX articles_domain ON articles(domain);
```

**Kiedy wybrać:** production 21M URL, multi-tenant, zespół potrzebuje query/dashboard.

### C. Hybrid: Parquet + DuckDB + Qdrant (tańsza + szybsza)

**Stos:**
- **Parquet** w S3 (lub lokalne) — append-only, snappy compressed
- **[DuckDB](https://duckdb.org/)** — embedded analytical SQL, czyta parquet bezpośrednio
- **[Qdrant](https://qdrant.tech/)** — vector DB, REST API, open-source

**Plusy:**
- ✅ Tania storage: parquet kompresuje 4-5× (200 GB → ~50 GB)
- ✅ Append-only parquet = audit trail (immutable raw)
- ✅ DuckDB szybkie aggregations (analytical queries)
- ✅ Qdrant najlepszy dla vector + metadata filter
- ✅ Można reprocesować bez touchowania surowych danych
- ✅ Każdy komponent skalowalny niezależnie

**Minusy:**
- ⚠️ 3 systemy zamiast 1
- ⚠️ Synchronizacja Parquet ↔ Qdrant przez ETL
- ⚠️ Gorsze niż Postgres dla single-row updates (parquet jest immutable)

**Architektura:**

```
[ vLLM pipeline ]
       ↓
[ JSONL append-only ]   ← raw output, audit trail
       ↓
[ ETL: convert to Parquet, partitioned by domain or date ]
       ↓
   ┌───┴────┐
   ↓        ↓
[ S3/local Parquet ]   [ Qdrant ]
   ↓                       ↓
[ DuckDB query ]       [ Vector search ]
   (SQL analytics)        (semantic similarity)
```

**Query examples:**

```sql
-- DuckDB: agregacje w ms
SELECT category, COUNT(*) FROM 'articles/*.parquet' GROUP BY category;
SELECT type, COUNT(*) FROM 'entities/*.parquet'
  WHERE category = 'Quantity' GROUP BY type;
```

```python
# Qdrant: vector search z filter
qdrant.search(
  collection="articles",
  query_vector=embedding_of("witamina C dla odporności"),
  query_filter={"category": "Health, Medicine", "language": "pl"},
  limit=20
)
```

**Kiedy wybrać:** ML pipeline, batch processing, zespół data science, audit-first.

### D. ClickHouse (analytical OLAP-first)

**Plusy:**
- ✅ Najszybsze aggregations (kolumna-orientowane)
- ✅ Native vector search (od 24.x)
- ✅ Skala do petabajtów

**Minusy:**
- ⚠️ Slow OLTP (single-row updates wolne)
- ⚠️ Specyficzny dialekt SQL (różny od PostgreSQL)
- ⚠️ Sterea krzywa nauki

**Kiedy wybrać:** dominują analytics queries, mniej standardowy SELECT BY ID.

## Embeddingi — wybór modelu

| Model | Dim | Languages | Speed (CPU) | License |
|---|---|---|---|---|
| **BGE-M3** | 1024 | 100+ (PL excellent) | ~50 docs/s | MIT |
| **jina-embeddings-v3** | 1024 | 89 langs | ~80 docs/s | CC-BY-NC |
| `intfloat/multilingual-e5-large` | 1024 | 100+ | ~40 docs/s | MIT |
| `Snowflake/snowflake-arctic-embed-m-v2.0` | 768 | 100+ | ~120 docs/s | Apache 2.0 |

**Rekomendacja:** **BGE-M3** dla SEO content (multilingual, MIT, sprawdzony dla PL).

**Co embedujemy?** 3 poziomy:

1. **Article embedding** — `f"{title} {meta_description} {article_summary}"` (~300 tokenów) → 1 embedding per artykuł.
2. **Entity embedding** — `f"{name} | {type} | {category}"` → 1 embedding per encja.
3. **Combined search** — query → encode → search article_embeddings (shortlist 100) → rerank by entity_embeddings cosine sum.

**Storage:**

| Layer | Count | Size (FP16) |
|---|---|---|
| Article embeddings | 21M | 21M × 1024 × 2 B = **41 GB** |
| Entity embeddings | 460M | 460M × 1024 × 2 B = **900 GB** |
| Article+entity total | | **~940 GB** |

Przy FP8 quantization (BGE-M3 obsługuje matryoshka — 256/512/768/1024 dim): **~470 GB**.

## Rekomendacja etapowa

```
Phase 5-6 (Spark dev/staging, 100-1000 URL):
  → JSONL (już mamy) + SQLite + sqlite-vec
  → setup: 5 min, zero DevOps

Phase 7 (RunPod 5090 dev, ~5000 URL):
  → JSONL + SQLite (lokalnie na pod)
  → migration test: SQLite → PostgreSQL local

Phase 8-9 (RunPod 5090 prod, 21M URL):
  → Wybór:
    A. PostgreSQL + pgvector (managed RDS lub własny serwer)
       — koszt $100-300/m, łatwy zespół, multi-tenant ready
    B. Parquet + DuckDB + Qdrant (S3)
       — koszt $20-100/m storage, lepsza analytics, więcej kodu

  Decyzja po Phase 8 (wynik performance test).
```

## Praktyczny ETL plan (JSONL → DB)

```python
# scripts/etl_load.py (przyszły)
import json
import psycopg
from pathlib import Path
from sentence_transformers import SentenceTransformer

model = SentenceTransformer("BAAI/bge-m3")

with psycopg.connect(...) as conn:
    cur = conn.cursor()
    cur.copy(
        "COPY articles (url_hash, url, ..., embedding) FROM STDIN"
    ) as copy:
        for line in open("result/final.jsonl"):
            r = json.loads(line)
            text_for_emb = f"{r['title']} {r['meta_description']} {r['article_summary']}"
            emb = model.encode(text_for_emb)
            copy.write_row((r["url_hash"], r["url"], ..., emb.tolist()))
```

Throughput szacowany: ~5k records/s na średnim sprzęcie. 21M / 5k = **~70 minut ETL**.

## Otwarte decyzje

- [ ] Czy embeddingi liczymy w pipeline (Step 3) czy osobny ETL po Step 2?
- [ ] Per-entity czy per-article embeddingi? (zależy od use case search)
- [ ] FP16 vs FP8 dla wektorów (trade-off accuracy vs storage)
- [ ] Indeks HNSW (bez recall trade-off) vs IVFFlat (większa skala) w pgvector
- [ ] Czy raw JSONL trzymamy długoterminowo (audit) czy archiwizujemy po ETL

Decyzja po Phase 5 (mamy próbkę 155 URL z metadata) — wtedy wybierzemy embedding model i implementujemy ETL.

## Linki

- sqlite-vec: https://github.com/asg017/sqlite-vec
- pgvector: https://github.com/pgvector/pgvector
- DuckDB: https://duckdb.org/
- Qdrant: https://qdrant.tech/
- BGE-M3: https://huggingface.co/BAAI/bge-m3
- jina-embeddings-v3: https://huggingface.co/jinaai/jina-embeddings-v3
