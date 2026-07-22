FROM python:3.11-slim

WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

# Install Python dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Create .env from example if not exists
RUN if [ ! -f .env ]; then cp .env.example .env; fi

# Expose port
EXPOSE 8080

# Run
CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8080"]
