FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       iperf3 \
       iputils-ping \
       nmap \
       dnsutils \
       traceroute \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app.py .
COPY version.py .
COPY templates ./templates

ENV PYTHONUNBUFFERED=1
EXPOSE 8766

CMD ["python", "app.py"]
