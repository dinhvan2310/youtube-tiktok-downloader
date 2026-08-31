"""FastAPI entry point used by the web/Electron application."""

import uvicorn

if __name__ == "__main__":
    uvicorn.run("backend.main:app", host="127.0.0.1", port=8765)
