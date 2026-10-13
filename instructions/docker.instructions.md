---
description: "Docker and Docker Compose guardrails"
applyTo: '**/Dockerfile,**/Dockerfile.*,**/docker-compose*.yml,**/docker-compose*.yaml,**/.dockerignore'
---

# Docker and Docker Compose Standards

## Dockerfile

### Multi-stage Builds

Keep final images small. Never ship build tools or source in production.

```dockerfile
# ✅ CORRECT - separate build and runtime stages
FROM node:22-slim AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci && npm run build

FROM node:22-slim
WORKDIR /app
COPY --from=build /app/dist ./dist
CMD ["node", "dist/index.js"]
```

### Non-root User

Always create and switch to a non-root user: `RUN groupadd -r app && useradd -r -g app app` then `USER app`.

### Layer Ordering and .dockerignore

- Copy dependency manifests before source code so dependency layers cache.
- Always include a `.dockerignore`. Exclude at minimum: `.git`, `node_modules`, `.env*`, `*.log`, `dist`, `__pycache__`, `.venv`.
- Never send `.env` files into the build context.
- Pin base images to a specific version. Never use `latest`.

## Docker Compose

### Service Naming

Lowercase kebab-case. Name by role, not technology (`cache:` not `redis:`, `web-api:` not `Node_App:`).

### Health Checks and depends_on

Define healthchecks. Use `depends_on` with `condition: service_healthy`.

```yaml
services:
  db:
    image: postgres:16.3
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U postgres"]
      interval: 5s
      timeout: 3s
      retries: 5
  web-api:
    depends_on:
      db:
        condition: service_healthy
```

### Environment Variables

Never hardcode secrets in compose files. Use `env_file` or host passthrough.

```yaml
# ✅ CORRECT
environment:
  - DATABASE_URL        # passthrough from host
env_file:
  - .env

# ❌ WRONG
environment:
  - DATABASE_URL=postgres://admin:s3cret@db:5432/app
```

### Restart Policies

Set a restart policy on every long-running service. Quote `"no"` - bare `no` is YAML false.

```yaml
web-api:
  restart: unless-stopped
one-off-migration:
  restart: "no"
```

### Volumes

Named volumes for persistent data. Bind mounts are for development only.

```yaml
# ✅ CORRECT                              # ❌ WRONG
volumes:                                  volumes:
  db-data:                                  - ./pgdata:/var/lib/postgresql/data
services:
  db:
    volumes:
      - db-data:/var/lib/postgresql/data
```

### Networks

Define explicit networks when services need isolation. Do not rely solely on the default network.

```yaml
services:
  web-api:
    networks: [frontend, backend]
  db:
    networks: [backend]  # not reachable from frontend
```

### Image Pinning

Pin images to a specific version. Never use `latest` in production.
