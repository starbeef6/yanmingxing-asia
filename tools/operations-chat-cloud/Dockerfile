FROM python:3.12-slim
WORKDIR /app
COPY server.py /app/server.py
ENV PYTHONUNBUFFERED=1
USER 65534:65534
EXPOSE 8080
CMD ["python", "/app/server.py"]
