# Use official Python lightweight image
FROM python:3.12-slim

# Set working directory
WORKDIR /app

# Install system dependencies (build tools, git, audio libraries)
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    curl \
    git \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-download Silero VAD weights during build
COPY agent.py calendar_tool.py ./
RUN python -c "from livekit.plugins import silero; silero.VAD.load()"

# Copy application files
COPY . .

# Set environment variables
ENV PYTHONUNBUFFERED=1

# Run the agent in production start mode
CMD ["python", "agent.py", "start"]
