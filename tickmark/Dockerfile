FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml .
COPY tickmark ./tickmark
RUN pip install --no-cache-dir .
CMD ["uvicorn", "tickmark.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
