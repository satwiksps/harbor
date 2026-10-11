FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 UV_LINK_MODE=copy UV_CACHE_DIR=/tmp/uv-cache
WORKDIR /app
RUN pip install --no-cache-dir uv==0.12.23
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev
COPY backend ./backend
COPY upstreams ./upstreams
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
ENV PATH="/app/.venv/bin:$PATH"
USER 10001:10001
EXPOSE 8000 8001
CMD ["uvicorn", "backend.app:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]
