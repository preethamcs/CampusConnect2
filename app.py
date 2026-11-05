import os
from typing import Any
from uuid import UUID
from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions
from postgrest import APIError

load_dotenv()

supabase_url = os.environ.get("SUPABASE_URL") or os.environ.get("NEXT_PUBLIC_SUPABASE_URL")
supabase_key = os.environ.get("SUPABASE_PUBLISHABLE_KEY")
if not supabase_url or not supabase_key:
    raise RuntimeError("SUPABASE_URL and SUPABASE_PUBLISHABLE_KEY must be configured.")

app = FastAPI()
supabase: Client = create_client(supabase_url, supabase_key)


@app.exception_handler(HTTPException)
async def handle_http_exception(request: Request, error: HTTPException):
    return JSONResponse(
        status_code=error.status_code,
        content={"error": error.detail},
        headers=error.headers,
    )


def authenticated_client(
    authorization: str | None = Header(default=None),
) -> tuple[Client, Any]:
    authorization = authorization or ""
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(status_code=401, detail="A valid bearer token is required.")

    client = create_client(
        supabase_url,
        supabase_key,
        ClientOptions(headers={"Authorization": f"Bearer {token}"}),
    )
    try:
        user = client.auth.get_user(token).user
    except Exception:
        raise HTTPException(status_code=401, detail="A valid bearer token is required.")
    return client, user


def validate_event_id(event_id: str) -> str:
    try:
        UUID(event_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Invalid event ID.")
    return event_id


@app.get('/', response_class=HTMLResponse)
def index():
    try:
        response = supabase.table('users').select("*").execute()
    except APIError as error:
        return HTMLResponse(
            content=f'<p>Error loading users: {error.message}</p>',
            status_code=500,
        )

    users = response.data

    html = '<h1> users</h1><ul>'
    for user in users:
        html += f'<li>{user["email"]}</li>'
    html += '</ul>'

    return html


@app.get('/api/events')
def list_events():
    try:
        response = supabase.table('events').select('*').execute()
        return response.data
    except APIError as error:
        return JSONResponse({"error": error.message}, status_code=500)


@app.get('/api/events/{event_id}')
def get_event(event_id):
    try:
        validate_event_id(event_id)
        response = supabase.table('events').select('*').eq(
            'event_id', event_id
        ).limit(1).execute()
        if not response.data:
            raise HTTPException(status_code=404, detail="Event not found.")
        return response.data[0]
    except HTTPException:
        raise
    except APIError as error:
        return JSONResponse({"error": error.message}, status_code=500)


@app.post('/api/registrations')
def create_registration(payload: dict[str, Any], auth=Depends(authenticated_client)):
    client, user = auth
    event_id = payload.get('event_id')
    try:
        UUID(event_id)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="A valid event_id is required.")

    try:
        event_response = client.table('events').select(
            'event_id, capacity'
        ).eq('event_id', event_id).limit(1).execute()
        if not event_response.data:
            raise HTTPException(status_code=404, detail="Event not found.")

        existing = client.table('registration').select('id').eq(
            'student_id', user.id
        ).eq('event_id', event_id).limit(1).execute()
        if existing.data:
            raise HTTPException(status_code=409, detail="Already registered for this event.")

        capacity = event_response.data[0].get('capacity')
        if capacity is not None:
            registrations = client.table('registration').select(
                'id', count='exact'
            ).eq('event_id', event_id).execute()
            if registrations.count is not None and registrations.count >= capacity:
                raise HTTPException(status_code=409, detail="Event is at capacity.")

        response = client.table('registration').insert({
            'student_id': user.id,
            'event_id': event_id,
        }).execute()
        registration = response.data[0] if response.data else None
        return JSONResponse(registration, status_code=201)
    except HTTPException:
        raise
    except APIError as error:
        if error.code == '23505':
            raise HTTPException(status_code=409, detail="Already registered for this event.")
        return JSONResponse({"error": error.message}, status_code=500)


@app.get('/api/me/registrations')
def list_my_registrations(auth=Depends(authenticated_client)):
    client, user = auth
    try:
        response = client.table('registration').select('*').eq(
            'student_id', user.id
        ).execute()
        return response.data
    except APIError as error:
        return JSONResponse({"error": error.message}, status_code=500)


@app.get('/api/events/{event_id}/registrations')
def list_event_registrations(event_id: str, auth=Depends(authenticated_client)):
    client, user = auth
    try:
        validate_event_id(event_id)
        event_response = client.table('events').select(
            'event_id, student_id'
        ).eq('event_id', event_id).limit(1).execute()
        if not event_response.data:
            raise HTTPException(status_code=404, detail="Event not found.")
        if event_response.data[0]['student_id'] != user.id:
            raise HTTPException(
                status_code=403,
                detail="Only the event organizer can view registrations.",
            )

        response = client.table('registration').select('*').eq(
            'event_id', event_id
        ).execute()
        return response.data
    except HTTPException:
        raise
    except APIError as error:
        return JSONResponse({"error": error.message}, status_code=500)
