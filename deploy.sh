#!/bin/bash
clear

BASE=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )

scp \
    manifest.json \
    sony_web_tv.py \
    tv_remote.html \
    icon_192x192.png \
    tv.toml \
    \
    negro:/media/storage/data/sony-tv-control
