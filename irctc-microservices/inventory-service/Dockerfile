FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app.py models.py locks.py allocation.py promotion.py seed.py ./
COPY tests/ ./tests/
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
