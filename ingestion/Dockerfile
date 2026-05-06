FROM python:3.11-slim
RUN pip install imcpy psycopg2-binary shapely pandas
WORKDIR /app
COPY ingest.py .
CMD ["python3", "ingest.py"]
