# Use official Python 3.11
FROM python:3.11-slim

# Set working directory
WORKDIR /app

# System dependencies for Playwright
RUN apt-get update && apt-get install -y \
    curl wget gnupg ca-certificates \
    fonts-liberation libnss3 libx11-xcb1 libxcomposite1 \
    libxdamage1 libxrandr2 libasound2 libatk1.0-0 libcups2 \
    libxss1 libgtk-3-0 libgbm1 libpango-1.0-0 libatk-bridge2.0-0 \
    libdrm2 libxinerama1 libglib2.0-0 libfontconfig1 libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Copy requirements and install Python deps
COPY requirements.txt .
RUN pip install --upgrade pip
RUN pip install -r requirements.txt

# Install Playwright browsers
RUN pip install playwright
RUN playwright install --with-deps

# Copy bot code
COPY . .

# Expose port (if needed)
EXPOSE 8080

# Start bot
CMD ["python", "bot.py"]








