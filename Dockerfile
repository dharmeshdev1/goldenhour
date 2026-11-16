# Goldenhour, self-hosted: the tracer, the API server, the Guard and the web app in one image.
# Base images are pinned by digest (Dependabot proposes updates), like every action in CI.
FROM node:26-slim@sha256:5adad64f9a1fca2d51972caa65983634c8a774eb300d72b20f7f2a0f60368072 AS web
WORKDIR /web
COPY web/package.json web/pnpm-lock.yaml ./
# Node 25 and later no longer ship corepack: install the pnpm version package.json names.
RUN npm install -g "$(node -p "require('./package.json').packageManager")" && pnpm install --frozen-lockfile
COPY web ./
ENV NEXT_PUBLIC_BASE_PATH="" NEXT_TELEMETRY_DISABLED=1
RUN pnpm build

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928 AS build
COPY --from=ghcr.io/astral-sh/uv:0.9.5@sha256:f459f6f73a8c4ef5d69f4e6fbbdb8af751d6fa40ec34b39a1ab469acd6e289b7 /uv /usr/local/bin/uv
WORKDIR /app
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
COPY cases ./cases
COPY labels ./labels
COPY policies ./policies
RUN uv sync --locked --no-dev --extra all --no-editable

FROM python:3.15-slim@sha256:65a65fc7b30b06cb6df4930fd4650697cb23f4c2698ab81fc87e4b031bc36928
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
