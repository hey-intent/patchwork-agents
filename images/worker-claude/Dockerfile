FROM ubuntu:22.04

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /tmp

RUN apt-get update && apt-get install -y \
    curl ca-certificates git bash jq procps \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user (claude CLI refuses --dangerously-skip-permissions as root)
RUN useradd -m -s /bin/bash worker
USER worker

# install claude CLI as worker user
RUN curl -fsSL https://claude.ai/install.sh | bash

# add provider scripts and run wrapper
WORKDIR /app
COPY --chown=worker:worker images/worker-claude/run.sh /app/run.sh
COPY --chown=worker:worker providers/ /app/providers/
RUN sed -i 's/\r$//' /app/run.sh /app/providers/*.sh \
 && chmod +x /app/run.sh /app/providers/*.sh

ENV PATH="/home/worker/.local/bin:${PATH}"
WORKDIR /work

ENTRYPOINT ["/app/run.sh"]
CMD []
