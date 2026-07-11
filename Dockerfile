FROM python:3.12-alpine

WORKDIR /site
COPY . .

# Cloud Run provides PORT; this process serves only the static portal files.
CMD ["sh", "-c", "python -m http.server ${PORT:-8080} --directory /site"]
