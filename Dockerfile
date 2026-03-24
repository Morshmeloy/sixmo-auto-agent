FROM python:3.13-slim

RUN apt-get update && apt-get install -y \
    wget \
    gnupg \
    libglib2.0-0 \
    libnss3 \
    libx11-xcb1 \
    libxcb1 \
    libxcomposite1 \
    libxcursor1 \
    libxdamage1 \
    libxrandr2 \
    libgbm1 \
    libasound2 \
    libatk-bridge2.0-0 \
    libgtk-3-0 \
    libx11-6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

RUN playwright install chromium

COPY agent.py .

CMD ["python", "agent.py"]