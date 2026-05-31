FROM python:3.11-slim
LABEL maintainer="Yaron"

ENV PYTHONUNBUFFERED=1

ARG DEV=False

WORKDIR /app

COPY requirements.txt .
COPY requirements.dev.txt .

RUN python -m venv /py && \
    /py/bin/pip install --upgrade pip && \
    /py/bin/pip install -r requirements.txt && \
    if [ "$DEV" = "true" ]; \
        then /py/bin/pip install -r requirements.dev.txt ; \
        fi && \
    adduser --disabled-password --no-create-home django-user

COPY . .

RUN mkdir -p /app/staticfiles && \
    chown django-user:django-user /app/staticfiles && \
    chmod +x /app/entrypoint.sh

ENV PATH="/py/bin:$PATH"

EXPOSE 8000

USER django-user

CMD ["/app/entrypoint.sh"]
