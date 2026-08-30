FROM node:22-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    procps curl ca-certificates gnupg jq git unzip ncurses-term \
    && rm -rf /var/lib/apt/lists/*

ENV LANG=C.UTF-8 LC_ALL=C.UTF-8 SHELL=/bin/bash

# Zellij
ARG ZELLIJ_VERSION=0.41.2
RUN curl -fsSL "https://github.com/zellij-org/zellij/releases/download/v${ZELLIJ_VERSION}/zellij-x86_64-unknown-linux-musl.tar.gz" \
    | tar -xz -C /usr/local/bin \
    && chmod +x /usr/local/bin/zellij

# uv（mcp-server-fetch / mcp-server-git 用）
RUN curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

# Claude Code
RUN npm install -g @anthropic-ai/claude-code \
    && node /usr/local/lib/node_modules/@anthropic-ai/claude-code/install.cjs

# Antigravity CLI (agy)
RUN curl -fsSL https://antigravity.google/cli/install.sh | bash \
    && mv /root/.local/bin/agy /usr/local/bin/agy

# gws (Google Workspace CLI)
RUN npm install -g @googleworkspace/cli

# Google Cloud SDK (gcloud)
RUN echo "deb [signed-by=/usr/share/keyrings/cloud.google.gpg] https://packages.cloud.google.com/apt cloud-sdk main" \
      > /etc/apt/sources.list.d/google-cloud-sdk.list \
    && curl -fsSL https://packages.cloud.google.com/apt/doc/apt-key.gpg \
      | gpg --dearmor -o /usr/share/keyrings/cloud.google.gpg \
    && apt-get update \
    && apt-get install -y --no-install-recommends google-cloud-cli \
    && rm -rf /var/lib/apt/lists/*

# notebooklm-mcp-cli (uv tool) - redirect the install to a shared location
# instead of root's home, same reasoning as ubuntu/remote-agy/setup.sh
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin uv tool install notebooklm-mcp-cli

# markitdown (uv tool) - IR PDF を Markdown に変換してローカルで直接解析するため
RUN UV_TOOL_DIR=/opt/uv-tools UV_TOOL_BIN_DIR=/usr/local/bin uv tool install "markitdown[pdf]"

# claude-env: ローカルパッケージを COPY して install + setup
COPY packages/claude-env /tmp/claude-env
RUN npm install -g /tmp/claude-env \
    && claude-env install \
    && HOME=/home/node claude-env setup \
    && chown -R node:node /home/node \
    && rm -rf /tmp/claude-env

WORKDIR /workspace
USER node
