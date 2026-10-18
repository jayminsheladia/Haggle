# One container runs every Haggle agent (mailbox mode; no inbound ports needed).
FROM python:3.12-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
ENV PYTHONUNBUFFERED=1

# Keys and seeds come from the host's environment variables (same names as .env.example).
CMD ["python", "-m", "scripts.run_all"]
