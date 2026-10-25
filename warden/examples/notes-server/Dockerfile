# The notes server as a container (step 25): same code as `uv run server.py`, dependencies pinned
# like its header says. Listens on 0.0.0.0:8100 inside the container; Azure gives it internal ingress only.
FROM python:3.12-slim
WORKDIR /app
RUN pip install --no-cache-dir "mcp==2.3.0" "pyjwt[crypto]"
COPY server.py .
ENV HOST=0.0.0.0 PORT=8100
CMD ["python", "server.py"]
