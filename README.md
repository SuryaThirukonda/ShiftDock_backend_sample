# Shiftdock backend sample

**This is just a sample from a platform I built with a team of 3, called shiftDock. ShiftDock is a part of WaiterDock, which we are developing for a restaurant, and plan to expand to other restaurants. Since we already deployed our platform, I only included a small subset of our backend.**

This sampe focuses on the core parts of the backend:
- employee and manager auth
- employee management
- shift creation + assignment
- checkin/checkout logic
- task completition and tracking

#tech stack
- FastAPI
- SQLalchemy (for ORM)
- Pydantic 
- PostgreSQL
- SQlite for local cache

#project structure
- app/main.py:  initializes the fastAPI app and registers routers (there were more that arent included in this repo)
- app/models.py : defines the SQLalchemy database models
- app/schemas.py: defines pydantic request/response schemas
- app/auth.py: handles auth
- app/routers/ : contains more API endpoints for employees, shifts, tasks, and checkins/checkouts. I made this prevent having too much clutter in main.py

#running locally
- pythom -m venv .venv
- .venv\Scripts\activate
- pip install -r requirements.txt
- python -m uvicorn app.main:app --reload

