FROM mcr.microsoft.com/playwright/python:v1.42.0-focal

WORKDIR /app

# Copy requirements and install
COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

# Copy bot code
COPY . .

EXPOSE 8080

CMD ["python", "bot.py"]






