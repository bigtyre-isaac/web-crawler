FROM python:3.12-slim

WORKDIR /app

# Install dependencies
RUN pip install --no-cache-dir scrapy pymysql

COPY crawler.py /app/crawler.py

ENV PYTHONUNBUFFERED=1

# Run the spider once and exit
ENTRYPOINT ["scrapy", "runspider", "/app/crawler.py"]
