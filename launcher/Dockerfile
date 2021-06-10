FROM docker.io/library/python:3.7-slim

WORKDIR /app

COPY hebi-manifest-templates /app/hebi-manifest-templates
COPY launcher.py /app
COPY requirements.txt /app

RUN pip install -r requirements.txt

CMD ["python3.7", "launcher.py"]
