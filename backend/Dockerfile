FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY tests ./tests
COPY wsgi.py .
EXPOSE 5000
CMD ["gunicorn","-w","1","-b","0.0.0.0:5000","wsgi:app","--timeout","30"]
