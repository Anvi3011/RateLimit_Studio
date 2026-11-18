FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    PYTHONPATH=/studio/backend:/studio/worker \
    SUPERVISOR_STATUS_FILE=/tmp/studio-processes.json SCHEMA_INIT=false
WORKDIR /studio
COPY backend/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt \
    && useradd --create-home --uid 10001 studio
COPY backend/app ./backend/app
COPY backend/wsgi.py ./backend/wsgi.py
COPY worker/*.py ./worker/
COPY deployment/public/supervisor.py ./deployment/public/supervisor.py
USER studio
EXPOSE 10000
CMD ["python", "deployment/public/supervisor.py"]
