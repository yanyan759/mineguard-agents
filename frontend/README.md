# MineGuard Agents Frontend

React + Vite + Tailwind CSS + Ant Design frontend for the MineGuard Agents FastAPI backend.

## Run

```bash
pnpm install
pnpm dev
```

By default the app talks to `http://localhost:8000` and `ws://localhost:8000`.
Override with `.env.local`:

```bash
VITE_API_BASE_URL=http://localhost:8000
VITE_WS_BASE_URL=ws://localhost:8000
```

## Backend Contract

- `POST /api/workflow/start` — 启动六 Agent 工作流
- `POST /api/workflow/{run_id}/approve` / `reject` / `cancel` — 人工审批与取消
- `GET /api/workflow/{run_id}/status` — 工作流运行状态
- `POST /api/upload` / `GET /api/files` / `GET /api/download` — 文件上传与管理
- `WebSocket /ws/{thread_id}` — 实时事件推送
