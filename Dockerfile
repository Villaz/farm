# Stage 1: Build
FROM python:3.14-slim

# Set working directory
WORKDIR /app

# Install UV package manager
RUN pip install --no-cache-dir uv

# Copy dependency files
COPY pyproject.toml uv.lock ./

# Install dependencies using uv
RUN uv sync --frozen

# Copy application code
COPY api/ ./api/
COPY init_db.py loader.py ./
COPY data/ ./data/

# Create directory for database volume
RUN mkdir -p /app/db

# Expose the API port
EXPOSE 8000

# Set environment variables
ENV DATABASE_URL=/app/db/cows.db
ENV PYTHONUNBUFFERED=1

# Health check to verify the API is running
HEALTHCHECK --interval=10s --timeout=5s --start-period=15s --retries=3 \
  CMD curl -f http://localhost:8000/health || exit 1

# Create a startup script to initialize database and run the server
RUN cat > /app/startup.sh << 'EOF'
#!/bin/bash
set -e
mkdir -p /app/db
cd /app
# Initialize database only if it doesn't exist
if [ ! -f /app/db/cows.db ]; then
    python -c "from init_db import init_db; init_db('/app/db/cows.db')"
fi
# Start the API server
exec uv run uvicorn api.main:app --host 0.0.0.0 --port 8000
EOF

RUN chmod +x /app/startup.sh

CMD ["/app/startup.sh"]
