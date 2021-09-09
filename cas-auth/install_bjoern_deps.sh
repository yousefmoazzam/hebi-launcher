#!/bin/bash

# need to install gcc, python3-dev, and libev-dev to install bjoern
if [[ "$FLASK_MODE" == "production" ]] ; then
  apt-get update && \
  apt-get install -y --no-install-recommends gcc python3-dev libev-dev && \
  rm -rf /var/lib/apt/lists/*
fi
