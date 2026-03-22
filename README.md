# autocoin

**지금은 빈 FastAPI 앱만 있습니다.** (`src/app.py` → `app`)

```bash
pip install -e .
autocoin-api
# 또는: PYTHONPATH=src uvicorn app:app --reload
```

Docker: `docker compose up --build` (DB + API). API 컨테이너는 DB 없이도 기동되며, 마이그레이션 단계는 제거된 상태입니다.

다음에 설계할 때 `pyproject.toml` 의존성·`src/` 구조를 처음부터 맞추면 됩니다.
