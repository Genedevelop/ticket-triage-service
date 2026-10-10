# --- Base image ---
FROM python:3.13-slim

# --- Python and pip behaviour ---
# no bytecode files, unbuffered output so log lines appear at once, no pip version notice
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1

# --- User and working directory ---
# the service runs as an unprivileged user
RUN useradd --create-home --uid 1000 app
WORKDIR /srv

# --- Dependencies ---
# installed before the source is copied, so a code change keeps this layer cached
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# --- Source ---
COPY . .
USER app

# --- Entry point ---
EXPOSE 8000
CMD ["uvicorn", "main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
