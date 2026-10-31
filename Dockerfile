FROM python:3.12-alpine
# eMudhra CA distrust (Oct 2026): current CA bundle
RUN apk upgrade --no-cache ca-certificates-bundle ca-certificates
WORKDIR /app
COPY artifact_ui.py server.py ./
ENV PORT=8080 PAGES_DIR=/tmp/pages PYTHONUNBUFFERED=1
EXPOSE 8080
USER nobody
CMD ["python", "-I", "server.py"]
