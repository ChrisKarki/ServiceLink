FROM python:3.13-slim

WORKDIR /app

# Prevent Python from writing pyc files and keep stdout unbuffered
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# Install dependencies
COPY requirements.txt .
RUN python -c 'with open("requirements.txt", "rb") as f: d = f.read(); open("requirements.txt", "w", encoding="utf-8").write(d.decode("utf-16le") if d.startswith(b"\xff\xfe") else d.decode("utf-8"))' \
    && pip install --no-cache-dir -r requirements.txt

# Copy application source code
COPY . .

# Expose port 5000 for Flask
EXPOSE 5000

# Run Flask server
CMD ["flask", "run", "--host=0.0.0.0", "--port=5000"]
