FROM python:3.12-slim AS builder

#create the app directory
RUN mkdir /app

# Set the working directory to /app
WORKDIR /app

# Set environment variables to optimize Python and Django for production
ENV PYTHONUNBUFFERED=1 
ENV PYTHONDONTWRITEBYTECODE=1

#Install dependencies variables for caching benefits
RUN pip install --upgrade pip
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Stage 2: Production stage 
FROM python:3.12-slim

RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/* && \
    useradd -m -r appuser && \
    mkdir -p /app && \
    chown -R appuser:appuser /app

# Copy the installed dependencies from the builder stage
COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# Set the working directory to /app
WORKDIR /app

# Copy the application code
COPY --chown=appuser:appuser . .

# Set environment variables for Django
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1

# Switch to the non-root user
USER appuser

# Expose the port that the application will run on
EXPOSE 8000

# Make the entrypoint script executable
RUN chmod +x /app/entrypoint.prod.sh

HEALTHCHECK --interval=30s --timeout=30s --start-period=5s --retries=3 CMD [ "curl", "-f", "http://localhost:8000/health/" ] || exit 1

# Start the application using Gunicorn
CMD ["/app/entrypoint.prod.sh"]

