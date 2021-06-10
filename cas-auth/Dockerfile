FROM docker.io/library/python:3.7-slim

WORKDIR /app

COPY cas-auth.py /app
COPY requirements.txt /app

RUN pip install -r requirements.txt

CMD ["python3.7", "cas-auth.py"]
