# syntax=docker/dockerfile:1
FROM python:3.15.0-slim-trixie@sha256:f48fda6d59b2b3437781d1c3ccf4aa0e26ae871de3cdd758b49ab45ee7291619 AS build
COPY --from=ghcr.io/astral-sh/uv:0.13.1@sha256:4a1f3666816dafadd20ceb71eac9a7d56d7ef30279f7d64048519ab901ada336 /uv /usr/local/bin/uv
WORKDIR /srv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy UV_PYTHON_DOWNLOADS=never
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv uv sync --frozen --no-dev
COPY app ./app
RUN python -m compileall -q app

FROM python:3.15.0-slim-trixie@sha256:f48fda6d59b2b3437781d1c3ccf4aa0e26ae871de3cdd758b49ab45ee7291619
WORKDIR /srv
COPY --from=build /srv /srv
ENV PATH=/srv/.venv/bin:$PATH PYTHONUNBUFFERED=1
USER 65534
EXPOSE 8080
# One Uvicorn worker per core: Uvicorn reads its worker count from WEB_CONCURRENCY, and the app
# splits DB_POOL_SIZE across that many workers. nproc respects the container's cpuset.
ENTRYPOINT ["/bin/sh", "-c", "export WEB_CONCURRENCY=\"$(nproc)\" && exec uvicorn app.main:app --host 0.0.0.0 --port 8080 --loop uvloop --http httptools --no-access-log --log-level warning"]
