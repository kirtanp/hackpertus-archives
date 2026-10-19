FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PORT=7860
# uv installs the locked runtime dependencies into /app/.venv; the dev, lint and notebook groups stay out of the
# image, and `uv sync --locked` fails the build rather than silently resolving something new.
COPY --from=ghcr.io/astral-sh/uv:0.9.20 /uv /usr/local/bin/uv
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy
RUN useradd -m -u 1000 user
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
ENV PATH="/app/.venv/bin:$PATH"
COPY --chown=user src/ src/
COPY --chown=user data/out/ data/out/
COPY --chown=user data/onboard/ data/onboard/
COPY --chown=user static/ static/
COPY --chown=user app.py .
USER user
EXPOSE 7860
CMD ["sh", "-c", "uvicorn app:app --host 0.0.0.0 --port ${PORT}"]
