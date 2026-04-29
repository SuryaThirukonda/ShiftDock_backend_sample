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
- SQLite

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
- by default, the backend uses a local SQLite database at ./shiftdock.db
- python -m uvicorn app.main:app --reload

#sqlite notes
- the SQLite database file is created automatically the first time the app starts and imports `app.main`
- table creation happens on startup through SQLAlchemy, so you do not need a separate database setup step


#first local auth setup
- open `http://127.0.0.1:8000/docs`
- if the database is empty, call `POST /employees/bootstrap-owner`

```json
{
  "name": "Local Owner",
  "email": "owner@example.com",
  "phone": "5551234567",
  "hourly_wage": 20,
  "pin": "1234"
}
```

- if bootstrap returns a conflict, that means an owner already exists in `shiftdock.db`
- after bootstrap, or any time after that, call `POST /employees/login`

```json
{
  "employee_id": 1,
  "pin": "1234"
}
```

- copy the `access_token` value from the login response
- click the `Authorize` button in Swagger
- paste only the token value into the auth field
- after authorizing, test `GET /employees/me` to confirm the token works
- manager and owner routes such as `POST /employees/`, `POST /tasks/`, and `POST /shifts/` will then use that token

