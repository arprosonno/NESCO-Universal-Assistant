# Use official Playwright Python image
FROM mcr.microsoft.com/playwright/python:v1.42.0-focal

WORKDIR /app

# Copy requirements first for caching
COPY requirements.txt .

RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Copy bot code
COPY . .

# Install only Chromium (faster, smaller)
RUN playwright install chromium

CMD ["python", "bot.py"]





