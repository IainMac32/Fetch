import atexit
from flask import Flask, request

from .config import Settings
from .fetch_orders import FetchOrdersError, load_order_calendar_entries
from .linq import LinqClient, incoming_message, verify_signature
from .service import HELP, SearchService
from .events import RecentEvents
from .fetch_chat import FetchChatError, run_fetch_chat


ALLOWED_FRONTEND_ORIGINS = {"http://localhost:5500", "http://127.0.0.1:5500"}

def create_app(settings=None, *, service=None, events=None):
    settings = settings or Settings.from_env()
    app = Flask(__name__, static_folder=None)
    app.config["MAX_CONTENT_LENGTH"] = 64 * 1024
    events = events if events is not None else RecentEvents()
    if service is None:
        service = SearchService(settings, LinqClient(settings.linq_api_key, settings.linq_base_url))
        atexit.register(service.close)
    app.extensions["search"] = service
    app.extensions["recent_events"] = events

    @app.after_request
    def privacy_headers(response):
        response.headers["Cache-Control"] = "no-store"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Content-Security-Policy"] = (
            "default-src 'none'; frame-ancestors 'none'; base-uri 'none'"
        )
        origin = request.headers.get("Origin")
        if origin in ALLOWED_FRONTEND_ORIGINS:
            response.headers["Access-Control-Allow-Origin"] = origin
            response.headers["Access-Control-Allow-Headers"] = "Content-Type"
            response.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
            response.headers["Vary"] = "Origin"
        return response

    @app.get("/")
    def home():
        return {"service": "MessageShopper", "message": HELP}

    @app.route("/api/fetch/chat", methods=["POST", "OPTIONS"])
    def fetch_chat():
        if request.method == "OPTIONS":
            return "", 204
        runtime_settings = Settings.from_env(messaging=False)
        payload = request.get_json(silent=True)
        if not isinstance(payload, dict):
            return {"error": "Expected a JSON object"}, 400
        message = payload.get("message")
        conversation = payload.get("conversation")
        state = payload.get("state")
        if not isinstance(message, str) or not message.strip():
            return {"error": "Message is required."}, 400
        if conversation is not None and not isinstance(conversation, list):
            return {"error": "Conversation must be a list."}, 400
        if state is not None and not isinstance(state, dict):
            return {"error": "State must be a JSON object."}, 400
        if not runtime_settings.openai_api_key:
            return {"error": "OPENAI_API_KEY is not configured on the server."}, 503
        try:
            result = run_fetch_chat(
                api_key=runtime_settings.openai_api_key,
                model=runtime_settings.openai_model,
                message=message.strip(),
                conversation=conversation or [],
                state=state or {},
            )
        except FetchChatError as exc:
            return {"error": str(exc)}, 502
        return result

    @app.route("/api/fetch/orders", methods=["GET", "OPTIONS"])
    def fetch_orders():
        if request.method == "OPTIONS":
            return "", 204
        runtime_settings = Settings.from_env(messaging=False)
        if not runtime_settings.mongo_uri:
            return {"error": "MONGO_URI is not configured on the server."}, 503
        if not runtime_settings.demo_user_handle:
            return {"error": "DEMO_USER_HANDLE is not configured on the server."}, 503
        try:
            entries = load_order_calendar_entries(
                mongo_uri=runtime_settings.mongo_uri,
                phone_number=runtime_settings.demo_user_handle,
            )
        except FetchOrdersError as exc:
            status_code = 503 if "configured" in str(exc) or "installed" in str(exc) else 502
            return {"error": str(exc)}, status_code
        return {"entries": entries, "count": len(entries)}

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
        if incoming.sender_handle != settings.demo_user_handle:
            return "", 200
        claimed = events.claim(incoming.event_id)
        if claimed is False:
            return "", 200
        if claimed is None:
            return {"error": "Please retry"}, 503
        if not service.submit(incoming):
            events.forget(incoming.event_id)
            return {"error": "Please retry"}, 503
        return "", 200

    return app


if __name__ == "__main__":
    create_app(Settings.from_env(messaging=False)).run(host="127.0.0.1", port=5000, threaded=True, use_reloader=False)
