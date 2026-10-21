FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY api.py drafts.py server.py ./

RUN useradd --no-create-home mcp
USER mcp

EXPOSE 8765
CMD ["python", "server.py"]
