import atexit
from flask import Flask, jsonify, request

from .browser import DoorDashBrowser
from .config import Settings
from .linq import LinqClient, incoming_message, verify_signature
from .service import ConnectionService
from .store import Store

def create_app(settings=None, *, service=None, store=None):
    settings = settings or Settings.from_env()
    app = Flask(__name__)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    owns_store = store is None
    store = store or Store(settings.mongo_uri, settings.credential_encryption_key)
    if owns_store:
        atexit.register(store.close)
    if service is None:
        service = ConnectionService(settings, store, LinqClient(settings.linq_api_key, settings.linq_base_url),
                                    DoorDashBrowser(settings, store))
        atexit.register(service.close)
    app.extensions["connections"] = service

    @app.after_request
    def privacy_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'self'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; img-src 'self' data:; object-src 'none'; "
            "base-uri 'none'; frame-ancestors 'none'; form-action 'none'"
        )
        return response

    @app.get("/")
    def home():
        return app.send_static_file("connect.html")

    @app.post("/linq-webhook")
    def webhook():
        if not verify_signature(settings.linq_webhook_secret, request.get_data(), request.headers):
            return {"error": "Invalid signature"}, 401
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return {"error": "Expected a JSON object"}, 400
        incoming = incoming_message(payload)
        if not incoming:
            return "", 200
        if incoming.event_id != request.headers.get("webhook-id"):
            return {"error": "Event ID mismatch"}, 400
        if not store.claim_event(incoming.event_id):
            return "", 200
        if not service.submit(incoming):
            store.forget_event(incoming.event_id)
            return {"error": "Please retry"}, 503
        return "", 200

    @app.get("/connect")
    def connect():
        return app.send_static_file("connect.html")

    def authorized_job():
        auth = request.headers.get("Authorization", "")
        return service.lookup(auth[7:]) if auth.startswith("Bearer ") else None

    @app.get("/api/handoff")
    def handoff():
        job = authorized_job()
        if not job:
            return {"error": "This link is invalid or has expired. Text CONNECT for a new one."}, 410
        return jsonify(job.snapshot())

    @app.post("/api/handoff/credentials")
    def credentials():
        job = authorized_job()
        if not job:
            return {"error": "This link is invalid or has expired."}, 410
        origin = request.headers.get("Origin")
        if origin and origin != settings.public_base_url:
            return {"error": "Invalid origin"}, 403
        data = request.get_json(silent=True)
        if not isinstance(data, dict):
            return {"error": "Expected DoorDash sign-in details."}, 400
        login, password = data.get("login"), data.get("password")
        if (not isinstance(login, str) or not 3 <= len(login.strip()) <= 254
                or not isinstance(password, str) or not 1 <= len(password) <= 1024):
            return {"error": "Enter your DoorDash email or phone and password."}, 400
        login = login.strip()
        accepted = job.submit_sign_in(
            login, password,
            lambda: store.save_credentials(job.user_key, login, password),
        )
        if not accepted:
            return {"error": "This connection is not accepting sign-in details."}, 409
        return "", 202

    @app.post("/api/handoff/code")
    def verification_code():
        job = authorized_job()
        if not job:
            return {"error": "This link is invalid or has expired."}, 410
        origin = request.headers.get("Origin")
        if origin and origin != settings.public_base_url:
            return {"error": "Invalid origin"}, 403
        data = request.get_json(silent=True)
        code = data.get("code") if isinstance(data, dict) else None
        if not isinstance(code, str) or not code.isdigit() or not 4 <= len(code) <= 8:
            return {"error": "Enter the 4 to 8 digit DoorDash code."}, 400
        if not job.submit_code(code):
            return {"error": "This connection is not waiting for a verification code."}, 409
        return "", 202

    return app
