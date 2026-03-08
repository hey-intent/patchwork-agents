FROM python:3.12-slim

ENV DEBIAN_FRONTEND=noninteractive
WORKDIR /tmp

RUN apt-get update && apt-get install -y \
    curl ca-certificates git bash jq procps ripgrep \
 && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN useradd -m -s /bin/bash worker
USER worker

# Install Aider
RUN pip install --user aider-chat

# Add provider scripts and run wrapper
WORKDIR /app
COPY --chown=worker:worker images/worker-aider/run.sh /app/run.sh
COPY --chown=worker:worker providers/ /app/providers/
RUN sed -i 's/\r$//' /app/run.sh /app/providers/*.sh \
 && chmod +x /app/run.sh /app/providers/*.sh

ENV PATH="/home/worker/.local/bin:${PATH}"
WORKDIR /work

ENTRYPOINT ["/app/run.sh"]
CMD []
