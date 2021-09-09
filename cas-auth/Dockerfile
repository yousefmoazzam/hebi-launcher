FROM docker.io/library/python:3.7-slim

ARG FLASK_MODE=development
ARG REQUIREMENTS=./requirements/dev.txt

ENV FLASK_MODE=${FLASK_MODE}

WORKDIR /app

COPY cas-auth.py /app
COPY requirements/common.txt /app
COPY ${REQUIREMENTS} /app/requirements.txt

ADD install_bjoern_deps.sh /install_bjoern_deps.sh
RUN /install_bjoern_deps.sh

RUN pip install -r requirements.txt

ADD remove_bjoern_deps.sh /remove_bjoern_deps.sh
RUN /remove_bjoern_deps.sh

CMD ["python3.7", "cas-auth.py"]
