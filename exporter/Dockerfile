FROM python:3.12-alpine

# Non-root user for security
RUN addgroup -S exporter && adduser -S exporter -G exporter

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY exporter.py .

USER exporter

EXPOSE 8000

CMD ["python", "-u", "exporter.py"]
