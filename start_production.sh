#!/bin/bash

echo "Starting ChamaKe Backend Server (Production)..."

# Get port from environment or default to 8000
PORT=${PORT:-8000}

# Start server
echo "Starting FastAPI server on port $PORT"
uvicorn server:app --host 0.0.0.0 --port $PORT
