# One Railway service contains the dashboard and worker so they share one SQLite volume.
FROM node:24-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    NODE_ENV=production

WORKDIR /app

# Python runs the scheduler; build tools are needed by native Node modules (better-sqlite3).
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3 python3-venv build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python worker dependencies in an isolated environment.
COPY requirements.txt /app/requirements.txt
RUN python3 -m venv /opt/worker-venv \
    && /opt/worker-venv/bin/pip install --no-cache-dir --upgrade pip \
    && /opt/worker-venv/bin/pip install --no-cache-dir -r /app/requirements.txt

# Install dashboard dependencies with the committed lockfile.
COPY dashboard/package.json dashboard/package-lock.json /app/dashboard/
RUN cd /app/dashboard && npm ci

# Include migrations, worker, and dashboard in the same image.
COPY . /app

# Build from the dashboard directory; runtime config resolves the repository root as /app.
RUN cd /app/dashboard && npm run build

EXPOSE 3000
CMD ["/bin/bash", "/app/railway-start.sh"]
