import os
from uuid import UUID
from flask import Flask, jsonify, request
from supabase import create_client, Client
from supabase.lib.client_options import ClientOptions
from postgrest import APIError
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)

supabase: Client = create_client(
    os.environ.get("SUPABASE_URL"),
    os.environ.get("SUPABASE_PUBLISHABLE_KEY")
)


def authenticated_client():
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        return None, None

    client = create_client(
        os.environ.get("SUPABASE_URL"),
        os.environ.get("SUPABASE_PUBLISHABLE_KEY"),
        ClientOptions(headers={"Authorization": f"Bearer {token}"}),
    )
    try:
        user = client.auth.get_user(token).user
    except Exception:
        return None, None
    return client, user


def unauthorized():
    return jsonify({"error": "A valid bearer token is required."}), 401


@app.route('/')
def index():
    try:
        response = supabase.table('users').select("*").execute()
    except APIError as error:
        return f'<p>Error loading users: {error.message}</p>'

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
        return jsonify(response.data)
    except APIError as error:
        return jsonify({"error": error.message}), 500


@app.get('/api/events/<event_id>')
def get_event(event_id):
    try:
        UUID(event_id)
        response = supabase.table('events').select('*').eq(
            'event_id', event_id
        ).limit(1).execute()
        if not response.data:
            return jsonify({"error": "Event not found."}), 404
        return jsonify(response.data[0])
    except ValueError:
        return jsonify({"error": "Invalid event ID."}), 400
    except APIError as error:
        return jsonify({"error": error.message}), 500


@app.post('/api/registrations')
def create_registration():
    client, user = authenticated_client()
    if user is None:
        return unauthorized()

    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object with event_id is required."}), 400
    event_id = payload.get('event_id')
    try:
        UUID(event_id)
    except (TypeError, ValueError):
        return jsonify({"error": "A valid event_id is required."}), 400

    try:
        event_response = client.table('events').select(
            'event_id, capacity'
        ).eq('event_id', event_id).limit(1).execute()
        if not event_response.data:
            return jsonify({"error": "Event not found."}), 404

        existing = client.table('registration').select('id').eq(
            'student_id', user.id
        ).eq('event_id', event_id).limit(1).execute()
        if existing.data:
            return jsonify({"error": "Already registered for this event."}), 409

        capacity = event_response.data[0].get('capacity')
        if capacity is not None:
            registrations = client.table('registration').select(
                'id', count='exact'
            ).eq('event_id', event_id).execute()
            if registrations.count is not None and registrations.count >= capacity:
                return jsonify({"error": "Event is at capacity."}), 409

        response = client.table('registration').insert({
            'student_id': user.id,
            'event_id': event_id,
        }).execute()
        registration = response.data[0] if response.data else None
        return jsonify(registration), 201
    except APIError as error:
        if error.code == '23505':
            return jsonify({"error": "Already registered for this event."}), 409
        return jsonify({"error": error.message}), 500


@app.get('/api/me/registrations')
def list_my_registrations():
    client, user = authenticated_client()
    if user is None:
        return unauthorized()

    try:
        response = client.table('registration').select('*').eq(
            'student_id', user.id
        ).execute()
        return jsonify(response.data)
    except APIError as error:
        return jsonify({"error": error.message}), 500


@app.get('/api/events/<event_id>/registrations')
def list_event_registrations(event_id):
    client, user = authenticated_client()
    if user is None:
        return unauthorized()

    try:
        UUID(event_id)
        event_response = client.table('events').select(
            'event_id, student_id'
        ).eq('event_id', event_id).limit(1).execute()
        if not event_response.data:
            return jsonify({"error": "Event not found."}), 404
        if event_response.data[0]['student_id'] != user.id:
            return jsonify({"error": "Only the event organizer can view registrations."}), 403

        response = client.table('registration').select('*').eq(
            'event_id', event_id
        ).execute()
        return jsonify(response.data)
    except ValueError:
        return jsonify({"error": "Invalid event ID."}), 400
    except APIError as error:
        return jsonify({"error": error.message}), 500


if __name__ == '__main__':
    app.run(debug=True)
