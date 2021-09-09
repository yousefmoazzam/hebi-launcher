#!/bin/bash

# libev-dev needs to be kept for bjoern to run, but the others can safely be
# removed
if [[ "$FLASK_MODE" == "production" ]] ; then
  apt-get purge -y --auto-remove gcc python3-dev
fi
