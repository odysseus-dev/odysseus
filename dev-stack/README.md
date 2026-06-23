# Odysseus Dev-Stack — Polyglot Developer Playground

A **separate**, opt-in Docker environment for your own Java + Python development,
with Kafka, PostgreSQL, Redis, and MongoDB on the side. It is intentionally
isolated from the Odysseus application:

- It does **not** run Odysseus.
- It does **not** modify the app's `Dockerfile` or `docker-compose.yml`.
- Nothing starts unless you explicitly pass a `--profile`.

Use it to learn/build event-driven backends, practice with SQL/NoSQL databases,
caching, and message streaming — the building blocks behind most web and AI
engineering work.

> **Run all commands from inside this `dev-stack/` directory.**

---

## 1. Prerequisites
- Docker Desktop (Windows/macOS) or Docker Engine + Compose plugin (Linux).
- Nothing else — Java, Python, and every service run in containers.

## 2. First run

```bash
cd dev-stack
cp .env.example .env        # optional — defaults already work (PowerShell: Copy-Item .env.example .env)
```

Then bring up only what you need (see profiles below).

## 3. Profiles — start only what you need

| Command (`docker compose -f docker-compose.dev.yml ...`) | Starts |
|---|---|
| `--profile java up -d` | Java dev container |
| `--profile python up -d` | Python dev container |
| `--profile dev up -d` | Java **and** Python containers |
| `--profile kafka up -d` | Kafka broker + Kafka UI |
| `--profile postgres up -d` | PostgreSQL + pgAdmin |
| `--profile redis up -d` | Redis |
| `--profile mongo up -d` | MongoDB + Mongo Express |
| `--profile db up -d` | Postgres + Redis + Mongo (+ their UIs) |
| `--profile qdrant up -d` | Qdrant vector database |
| `--profile pgvector up -d` | PostgreSQL 16 + pgvector extension |
| `--profile minio up -d` | MinIO S3-compatible object storage |
| `--profile ollama up -d` | Ollama local LLM server (port 11435, CPU-only) |
| `--profile ollama + gpu.ollama.yml up -d` | Ollama with **NVIDIA GPU passthrough** (see §10) |
| `--profile jupyter up -d` | Jupyter Lab (datascience-notebook) |
| `--profile ai up -d` | Qdrant + pgvector + Ollama + Jupyter (all AI/ML services) |
| `--profile all up -d` | Everything (15+ containers — prefer targeted profiles) |

You can combine profiles: `--profile java --profile postgres up -d`.

## 4. Ports & credentials

All ports bind to `127.0.0.1` by default. Credentials are **local-dev only** —
override them in `.env`.

| Service | URL / host:port | Default login |
|---|---|---|
| Kafka broker (from host) | `localhost:9092` | — |
| Kafka broker (from containers) | `kafka:19092` | — |
| Kafka UI | http://localhost:8085 | — |
| PostgreSQL | `localhost:5432` | `dev` / `devpass`, db `devdb` |
| pgAdmin | http://localhost:5050 | `dev@local.test` / `devpass` |
| Redis | `localhost:6379` | (no auth) |
| MongoDB | `localhost:27017` | `dev` / `devpass` |
| Mongo Express | http://localhost:8081 | (basic-auth disabled) |
| Qdrant (REST) | `localhost:6333` | — |
| Qdrant (gRPC) | `localhost:6334` | — |
| pgvector | `localhost:5433` | `dev` / `devpass`, db `vectordb` |
| MinIO API | `localhost:9000` | `minioadmin` / `minioadmin` |
| MinIO Console | http://localhost:9001 | `minioadmin` / `minioadmin` |
| Ollama (dev-stack) | `localhost:11435` | — |
| Jupyter Lab | http://localhost:8888 | token: `devtoken` |

> **None of these collide with Odysseus** (which uses 7000 / 8080 / 8091 / 8100 / 11434),
> so you can run the Odysseus app and this dev-stack at the same time.

## 5. Working in the language containers

The Java and Python containers stay alive (`sleep infinity`) so you shell in and
work interactively. Your code lives in `java/workspace/` and `python/workspace/`
on the host and is mounted live into the containers.

**Java:**
```bash
docker compose -f docker-compose.dev.yml exec java bash
# inside the container:
java -version
cd demo && mvn -q compile exec:java     # runs the included sample
gradle --version
```

**Python:**
```bash
docker compose -f docker-compose.dev.yml exec python bash
# inside the container:
python hello.py
pip install -r requirements.txt          # uv pip install ... also available (faster)
```

Maven (`~/.m2`), Gradle (`~/.gradle`), and pip caches are stored in named volumes,
so dependencies are not re-downloaded on every rebuild.

## 6. Connecting your code to the services

**Host name rule:**
- From **another container** (e.g. Java/Python dev container → Kafka), use the
  **service name**: `kafka:19092`, `postgres:5432`, `redis:6379`, `mongo:27017`.
- From your **host machine** (e.g. an app you run outside Docker), use
  `localhost` + the **published port**: `localhost:9092`, `localhost:5432`, etc.

**Connection strings (from a container):**
```
Postgres : postgresql://dev:devpass@postgres:5432/devdb
Redis    : redis://redis:6379/0
Mongo    : mongodb://dev:devpass@mongo:27017
Kafka    : kafka:19092            (bootstrap servers)
```

**Java client dependencies** to add to `pom.xml` as you go:
`org.apache.kafka:kafka-clients`, `org.postgresql:postgresql`,
`redis.clients:jedis`, `org.mongodb:mongodb-driver-sync`.

**Python client packages:** `confluent-kafka` (or `kafka-python`),
`psycopg[binary]` / `SQLAlchemy`, `redis`, `pymongo`.

## 7. Lifecycle / common commands

```bash
# status + logs
docker compose -f docker-compose.dev.yml ps
docker compose -f docker-compose.dev.yml logs -f kafka

# stop (keeps data volumes)
docker compose -f docker-compose.dev.yml --profile all down

# stop AND wipe all data volumes (fresh start)
docker compose -f docker-compose.dev.yml --profile all down -v

# rebuild the language images after editing their Dockerfiles
docker compose -f docker-compose.dev.yml build java python
```

## 8. Notes per service
- **Kafka** runs single-node in **KRaft** mode (no ZooKeeper) — fine for dev, not
  production (replication factors are 1). Create a topic from the Java/Python
  container or the Kafka UI.
- **Postgres** — the standard instance on port 5432. For vector embeddings, use
  the separate `pgvector` profile on port 5433 with the `pgvector/pgvector:pg16`
  image (PostgreSQL 16 + pgvector pre-installed).
- **Mongo Express** has basic-auth disabled for convenience; do not expose it.
- The Kafka UI image is `kafbat/kafka-ui` (the actively maintained fork of the
  former `provectuslabs/kafka-ui`).
- **Qdrant** — pure vector database built for similarity search. REST API at
  port 6333, gRPC at 6334. Connect from Python with `qdrant-client` or from Java
  with the Qdrant Java client.
- **pgvector** — same Postgres you know, plus the `vector` extension for
  storing and querying embeddings alongside relational data. Use it to compare
  vector-search approaches vs Qdrant.
- **MinIO** — S3-compatible object storage. Use it for datasets, model artifacts,
  uploaded files, or any blob storage your project needs. The Console at port
  9001 lets you create buckets and manage access keys via the UI. Note: no
  buckets are created on startup — create your first one at http://localhost:9001.
- **Ollama (dev-stack)** — runs on port 11435 to avoid clashing with the
  Odysseus project's own Ollama overlay (port 11434). Pull models like any
  Ollama: `docker compose -f docker-compose.dev.yml exec ollama-dev ollama pull llama3.2`.
  By default it runs on **CPU**. To use your NVIDIA GPU, see §10 below.
- **Jupyter** — Jupyter Lab with the `datascience-notebook` image (includes
  numpy, pandas, scikit-learn, matplotlib, etc.). Your code from
  `python/workspace/` is mounted at `/home/jovyan/work`. Access Lab at
  http://localhost:8888 with token `devtoken`.

## 9. Connection strings (from a container)

Add to Section 6's connection strings:
```
Qdrant  : http://qdrant:6333              (REST) / qdrant:6334 (gRPC)
pgvector: postgresql://dev:devpass@pgvector:5432/vectordb
MinIO   : http://minio:9000               (access key / secret key)
Ollama  : http://ollama-dev:11434         (API endpoint)
Jupyter : http://jupyter:8888             (container to container, rarely needed)
```

**Java client dependencies** (add to `pom.xml`):
`io.qdrant:client` (Qdrant), `org.postgresql:postgresql` (pgvector),
`io.minio:minio` (MinIO), `com.mongodb:mongodb-driver-sync` (MongoDB).

**Python client packages:** `qdrant-client`, `psycopg[binary]` (pgvector),
`minio`, `ollama` (or `openai` pointing at Ollama's endpoint).

---

## 10. GPU passthrough for Ollama

> **Only works on NVIDIA GPUs with the NVIDIA Container Toolkit.** Your system
> (RTX 4050) already has it — Docker detects the `nvidia` runtime.

An overlay file `gpu.ollama.yml` adds NVIDIA device reservations to the
`ollama-dev` service, following the same pattern as the Odysseus project's
`docker/gpu.nvidia.yml`.

**With GPU (start Ollama on your RTX 4050):**
```bash
cd dev-stack
docker compose -f docker-compose.dev.yml -f gpu.ollama.yml --profile ollama up -d
```

**With GPU + all AI services:**
```bash
docker compose -f docker-compose.dev.yml -f gpu.ollama.yml --profile ai up -d
```

**CPU-only (no GPU config needed — same as before):**
```bash
docker compose -f docker-compose.dev.yml --profile ollama up -d
```

**Verify GPU is being used inside the container:**
```bash
docker compose -f docker-compose.dev.yml -f gpu.ollama.yml exec ollama-dev nvidia-smi
```

Once running with GPU, Ollama loads models onto the GPU automatically. You'll
see significantly faster inference vs CPU, especially on larger models.
