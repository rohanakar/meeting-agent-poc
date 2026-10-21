from pathlib import Path

from fastapi import FastAPI, HTTPException, status
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from . import internal, meetings, search

UI_DIST = Path(__file__).parents[2] / "ui" / "dist"

app = FastAPI(title="meeting-agent")
app.include_router(meetings.router)
app.include_router(search.router)
app.include_router(internal.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


# The built UI, when there is one (the app image builds it). In development Vite serves it.
if UI_DIST.is_dir():
    app.mount("/assets", StaticFiles(directory=UI_DIST / "assets"), name="assets")

    @app.get("/{path:path}", include_in_schema=False)
    def ui(path: str) -> FileResponse:
        # Every page is the same index.html; the browser router picks the page.
        if path.startswith(("api/", "internal/")):
            raise HTTPException(status.HTTP_404_NOT_FOUND)
        return FileResponse(UI_DIST / "index.html")
