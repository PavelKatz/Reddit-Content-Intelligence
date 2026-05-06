FROM python:3.11-slim
WORKDIR /app
COPY server.py index.html ./
EXPOSE 3456
CMD ["python3", "server.py"]
