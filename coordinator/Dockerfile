FROM python:3.11-slim

WORKDIR /app
COPY coordinator/aggregator.py /app/aggregator.py
RUN pip install --no-cache-dir web3 requests torch
CMD ["python", "/app/aggregator.py"]
