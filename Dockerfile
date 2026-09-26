FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    SHOPLIST_DB=/data/shoplist.db

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY shoplist ./shoplist

RUN useradd --system --uid 1000 shoplist && mkdir /data && chown shoplist /data
USER shoplist
VOLUME /data
EXPOSE 8000

CMD ["uvicorn", "shoplist.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers"]
