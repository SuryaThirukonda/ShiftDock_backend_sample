from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .database import Base, engine
from .routers import checkin, employees, shifts, tasks

Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="ShiftDock Backend Sample",
    version="1.0.0",
    description="Backend-only FastAPI sample for restaurant shift operations.",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(employees.router, prefix="/employees", tags=["employees"])
app.include_router(tasks.router, prefix="/tasks", tags=["tasks"])
app.include_router(shifts.router, prefix="/shifts", tags=["shifts"])
app.include_router(checkin.router, prefix="", tags=["checkin"])


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}