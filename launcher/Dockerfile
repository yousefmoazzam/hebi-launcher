FROM docker.io/library/python:3.7-slim

# build arg for dev or prod image; default to dev value if a value is not
# provided
ARG FLASK_MODE=development

WORKDIR /app

COPY hebi-manifest-templates /app/hebi-manifest-templates
COPY launcher.py /app
COPY requirements.txt /app

RUN pip install -r requirements.txt

ENV FLASK_ENV=${FLASK_MODE}

CMD ["python3.7", "launcher.py"]
