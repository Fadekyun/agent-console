FROM python:3.11-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    tmux git openssh-client curl && \
    rm -rf /var/lib/apt/lists/*

RUN curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
    apt-get install -y --no-install-recommends nodejs && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY web/requirements.txt web/requirements.txt
RUN pip install --no-cache-dir -r web/requirements.txt

COPY package.json package-lock.json ./
RUN npm ci --omit=dev --no-audit --no-fund

COPY . .

RUN mkdir -p /app/agent-profiles && \
    cp -a agent-profiles/*.md /app/agent-profiles/ 2>/dev/null || true

COPY docker-entrypoint.sh /usr/local/bin/agent-console-entrypoint
RUN chmod 0755 /usr/local/bin/agent-console-entrypoint

ENV AGENT_CONSOLE_STATE_DIR=/data
ENV AGENT_CONSOLE_CONFIG_DIR=/config
ENV AGENT_CONSOLE_WORKSPACE_ROOT=/workspace
ENV AGENT_CONSOLE_PROFILE_DIR=/workspace/agent-profiles
ENV AGENT_CONSOLE_LAN_CIDR=127.0.0.1/32
ENV AGENT_CONSOLE_TRUSTED_HOSTS=localhost,127.0.0.1

VOLUME ["/data", "/config", "/workspace"]

EXPOSE 3210

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:3210/healthz || exit 1

ENTRYPOINT ["/usr/local/bin/agent-console-entrypoint"]
CMD ["uvicorn", "agent_console.web:app", "--host", "0.0.0.0", "--port", "3210", "--no-proxy-headers", "--loop", "asyncio"]
