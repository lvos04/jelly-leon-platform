"""Testbediening voor de bestaande qBittorrent-WebAPI en private torrent uit MSO."""

import secrets

from fastapi import FastAPI
from simkit.msomock import met_testbediening


def create_app() -> FastAPI:
    return met_testbediening("qbittorrent", lambda: {"username": "sim", "password": secrets.token_urlsafe(24)})
