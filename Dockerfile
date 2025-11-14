FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir scrapy pymysql

COPY src/spider.py /app/spider.py
COPY src/pipelines.py /app/pipelines.py

ENTRYPOINT ["scrapy", "runspider", "/app/spider.py"]
