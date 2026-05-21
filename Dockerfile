FROM python:3.12-slim

WORKDIR /app

# Install dependencies first (cached layer)
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Apply any pending migrations at startup
ENTRYPOINT ["sh", "-c", "flask db upgrade && exec flask run --host=0.0.0.0 --port=${PORT:-5000}"]

EXPOSE 5000
