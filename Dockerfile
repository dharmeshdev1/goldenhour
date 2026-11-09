# Goldenhour, self-hosted: the tracer, the API server, the Guard and the web app in one image.
# Base images are pinned by digest (Dependabot proposes updates), like every action in CI.
FROM node:24-slim@sha256:01d566e8d94d8c619591d78652babfccd3c6992a3dffa9ed5ab39e11cf6c70e9 AS web
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
RUN corepack enable && pnpm install --frozen-lockfile
COPY web ./
ENV NEXT_PUBLIC_BASE_PATH="" NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54 AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY cases ./cases
COPY labels ./labels
COPY policies ./policies
RUN uv sync --locked --no-dev --extra all --no-editable

FROM python:3.12-slim@sha256:4840d514c583a4d3d8cad3940e68dee6f0f6f3635c12fcd0a3d74c21a5b93a54
RUN useradd --create-home --uid 10001 goldenhour
WORKDIR /app
COPY --from=build /app/.venv /app/.venv
COPY --from=web /web/out /app/web
COPY LICENSE NOTICE ./
ENV PATH="/app/.venv/bin:$PATH" GOLDENHOUR_STATE=/data PYTHONUNBUFFERED=1
USER goldenhour
VOLUME ["/data"]
EXPOSE 8080
ENTRYPOINT ["goldenhour"]
CMD ["serve", "--host", "0.0.0.0", "--port", "8080"]
