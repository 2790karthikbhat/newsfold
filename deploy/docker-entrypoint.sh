#!/bin/sh
set -e
digest migrate
exec digest "$@"
