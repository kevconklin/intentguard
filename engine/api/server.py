"""Uvicorn entrypoint.

Run with:  uvicorn engine.api.server:app
Configuration is read from the environment (see engine.config.EngineConfig).
"""

from engine.api.app import create_app

app = create_app()
