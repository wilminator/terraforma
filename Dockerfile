# TerraFroma / Vanguard Tavern. One image for the game and its tests.
FROM python:3.14-slim AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN useradd --create-home --uid 1000 terraforma
WORKDIR /app

# Dependencies first, so code changes don't reinstall them.
COPY pyproject.toml README.md ./
COPY src ./src

# --- the game -------------------------------------------------------------
FROM base AS app
RUN pip install ".[all-databases]"
USER terraforma
# settings.toml is mounted into /app (see compose.yml): no env vars needed.
EXPOSE 8000
CMD ["uvicorn", "vanguard_tavern:app", "--factory", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]

# --- the tests ------------------------------------------------------------
FROM base AS test
RUN pip install -e ".[dev]"
COPY tests ./tests
USER terraforma
CMD ["pytest"]
