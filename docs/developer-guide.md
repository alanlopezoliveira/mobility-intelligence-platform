# Developer guide

## Local workflow

1. Copy .env.example to .env.
2. Run docker compose up -d.
3. Run make prepare-data.
4. Run make train.
5. Run make evaluate.
6. Start the backend and frontend using Docker or local commands.

## Testing

- py -3 -m pytest -q
- cd frontend && npm run build
- cd frontend && npm run lint
