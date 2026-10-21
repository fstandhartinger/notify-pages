FROM python:3.12-alpine
# eMudhra CA distrust (Oct 2026): current CA bundle
RUN apk add --no-cache --upgrade ca-certificates-bundle
WORKDIR /app
COPY server.py ./
ENV PORT=8080 PAGES_DIR=/tmp/pages PYTHONUNBUFFERED=1
EXPOSE 8080
USER nobody
CMD ["python", "-I", "server.py"]
