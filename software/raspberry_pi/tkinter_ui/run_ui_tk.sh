#!/bin/bash

export LIBGL_ALWAYS_SOFTWARE=1
export DISPLAY=:0
export XAUTHORITY=/home/emir/.Xauthority

APP_DIR="/home/emir/atmchamber/ui"

cd "$APP_DIR" || exit 1

python3 app.py
