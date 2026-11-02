#!/bin/zsh
set -eu
exec /usr/bin/python3 "${0:A:h}/起動.py" server "$@"
