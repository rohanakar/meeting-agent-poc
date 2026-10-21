from fastapi import FastAPI

from . import internal, meetings, search

app = FastAPI(title="meeting-agent")
app.include_router(meetings.router)
app.include_router(search.router)
app.include_router(internal.router)


@app.get("/api/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
