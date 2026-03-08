FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /tmp

RUN apt-get update && apt-get install -y \
    curl ca-certificates git bash jq procps \
 && rm -rf /var/lib/apt/lists/*

# Install Node.js (required for codex CLI)
RUN curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
 && apt-get install -y nodejs \
 && rm -rf /var/lib/apt/lists/*

# Install codex CLI globally (as root, before switching user)
RUN npm install -g @openai/codex

# Create non-root user
RUN useradd -m -s /bin/bash worker
USER worker
RUN git config --global user.name "patchwork-agent" \
 && git config --global user.email "patchwork-agent@users.noreply.github.com"

# Add provider scripts and run wrapper
WORKDIR /app
COPY --chown=worker:worker images/worker-codex/run.sh /app/run.sh
COPY --chown=worker:worker providers/ /app/providers/
RUN sed -i 's/\r$//' /app/run.sh /app/providers/*.sh \
 && chmod +x /app/run.sh /app/providers/*.sh

WORKDIR /work

ENTRYPOINT ["/app/run.sh"]
CMD []
