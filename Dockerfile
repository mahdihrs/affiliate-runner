FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# Ubuntu Noble (24.04) strips setuptools from system Python 3.12.
# playwright-stealth uses pkg_resources at import time.
# apt installs to /usr/lib/python3/dist-packages/ which is on sys.path.
RUN apt-get update && apt-get install -y python3-setuptools && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

RUN playwright install chromium

COPY . .

CMD ["python", "main.py"]
