FROM python:3.11-slim

WORKDIR /app
COPY . .

RUN pip install --upgrade pip && \
    pip install -r requirements.txt

ENV BOT_TOKEN=your_token_here

CMD ["python", "app.py"]
