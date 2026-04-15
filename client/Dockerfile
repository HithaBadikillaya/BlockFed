FROM python:3.11-slim

WORKDIR /app
COPY client/client.py /app/client.py
RUN pip install --no-cache-dir web3 requests torch torchvision
CMD ["python", "/app/client.py"]
