# Write the README again with proper closing triple quotes

readme_path = "/mnt/data/README_Practical_FastAPI_QC.md"

readme_content = """# Practical FastAPI (QC)

FastAPI + SQLAlchemy + Alembic, backed by Postgres. Docker Compose spins up the stack, runs migrations (init + seed), and serves the API.

## 1) Prerequisites

- Docker & Docker Compose
- (Optional) `psql` for DB debugging
- (Optional) `make` if you use the Makefile helpers

## 2) Setup

```bash
# 1) copy envs
cp .env.example .env

# 2) build & start (choose ONE of the following)
# A) if your compose file is at repo root:
docker compose up -d --build

# B) if your compose lives in api/docker/ (keep .env at repo root):
docker compose -f api/docker/docker-compose.yml --env-file .env up -d --build
```
