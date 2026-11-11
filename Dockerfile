FROM python:3.13-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir . && useradd --system --uid 10025 thinmail
USER thinmail
ENV THINMAIL_LISTEN=0.0.0.0:8088 PYTHONUNBUFFERED=1
EXPOSE 8088
HEALTHCHECK --interval=60s --timeout=5s CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8088/healthz')"
CMD ["thinmail"]
