"""Stub FastAPI minimal pentru etapa 1.

Rute de SSE și webui vin în etapa 2. În această etapă: doar health check.
"""

from fastapi import FastAPI

app = FastAPI(title="agent-zero-light")


@app.get("/health")
async def health():
    """Health check endpoint. Returnează status ok."""
    return {"status": "ok"}
