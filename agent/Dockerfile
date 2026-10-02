# AgentCore Runtime runs ARM64 containers. Build with --platform linux/arm64
# (build-and-push.sh passes it; on an Intel machine Docker will emulate).
FROM --platform=linux/arm64 public.ecr.aws/docker/library/python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# uv resolves from the committed lock file, so the image matches what was tested.
COPY --from=ghcr.io/astral-sh/uv:0.12.11 /uv /usr/local/bin/uv

# Dependencies first: this layer is cached while only agent code changes.
COPY pyproject.toml uv.lock ./
RUN uv export --frozen --no-dev --no-emit-project -o requirements.txt \
    && uv pip install --system --no-cache -r requirements.txt \
    && rm requirements.txt

COPY src/ ./

# AgentCore Runtime calls /invocations and /ping on 8080; BedrockAgentCoreApp serves both.
EXPOSE 8080

# Run as a non-root user.
RUN useradd --create-home --uid 10001 agent && chown -R agent:agent /app
USER agent

# opentelemetry-instrument emits the spans that AgentCore Observability and the
# evaluation runner read from CloudWatch. Without it there are no traces to score.
ENTRYPOINT ["opentelemetry-instrument", "python", "main.py"]
