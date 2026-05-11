# Agentic Framework UI

Next.js frontend for the existing FastAPI backend.

## Run

```bash
npm install
npm run dev
```

By default the UI calls:

```bash
http://localhost:8002/api/v1
```

Override it with:

```bash
NEXT_PUBLIC_API_BASE=http://localhost:8002/api/v1
```

From the repository root, `uv run python main.py both` starts FastAPI and this UI together.
