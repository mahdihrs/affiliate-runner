FROM mcr.microsoft.com/playwright/python:v1.49.1-noble

WORKDIR /app

# setuptools must exist BEFORE playwright-stealth is imported (it uses pkg_resources)
RUN pip install --no-cache-dir --break-system-packages setuptools

COPY requirements.txt .
RUN pip install --no-cache-dir --break-system-packages -r requirements.txt

RUN playwright install chromium

COPY . .

CMD ["python", "main.py"]
