import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    browserbase_api_key: str = ""
    browserbase_project_id: str = ""
    credential_encryption_key: str = ""
    linq_api_key: str = ""
    linq_webhook_secret: str = ""
    public_base_url: str = ""
    linq_base_url: str = "https://api.linqapp.com/api/partner/v3"
    mongo_uri: str = "mongodb://localhost:27017"
    sign_in_timeout: int = 600
    session_timeout: int = 900
    max_sessions: int = 2
    model_api_key: str = ""
    stagehand_model: str = ""
    demo_user_handle: str = ""

    @classmethod
    def from_env(cls, *, messaging=True):
        load_dotenv()
        required = ["BROWSERBASE_API_KEY", "DOORDASH_CREDENTIAL_KEY"]
        if messaging:
            required += ["LINQ_API_KEY", "LINQ_WEBHOOK_SECRET", "PUBLIC_BASE_URL", "DEMO_USER_HANDLE"]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError("Set these values in .env: " + ", ".join(missing))
        settings = cls(
            browserbase_api_key=os.getenv("BROWSERBASE_API_KEY", ""),
            browserbase_project_id=os.getenv("BROWSERBASE_PROJECT_ID", ""),
            credential_encryption_key=os.getenv("DOORDASH_CREDENTIAL_KEY", ""),
            mongo_uri=os.getenv("MONGODB_URI", "mongodb://localhost:27017"),
            linq_api_key=os.getenv("LINQ_API_KEY", ""),
            linq_webhook_secret=os.getenv("LINQ_WEBHOOK_SECRET", ""),
            public_base_url=os.getenv("PUBLIC_BASE_URL", "").rstrip("/"),
            linq_base_url=os.getenv("LINQ_BASE_URL", cls.linq_base_url).rstrip("/"),
            sign_in_timeout=int(os.getenv("SIGN_IN_TIMEOUT_SECONDS", "600")),
            session_timeout=int(os.getenv("BROWSER_SESSION_TIMEOUT_SECONDS", "900")),
            max_sessions=int(os.getenv("MAX_BROWSER_SESSIONS", "2")),
            model_api_key=os.getenv("MODEL_API_KEY", ""),
            stagehand_model=os.getenv("STAGEHAND_MODEL", ""),
            demo_user_handle=os.getenv("DEMO_USER_HANDLE", ""),
        )
        if messaging:
            url = urlsplit(settings.public_base_url)
            if url.scheme != "https" or not url.netloc or url.username or url.query or url.fragment or url.path:
                raise ValueError("PUBLIC_BASE_URL must be an HTTPS origin, such as https://shop.example.com")
        if settings.sign_in_timeout < 30 or settings.session_timeout < settings.sign_in_timeout + 120:
            raise ValueError("Session timeout must exceed sign-in timeout by at least 120 seconds.")
        if settings.max_sessions < 1:
            raise ValueError("MAX_BROWSER_SESSIONS must be positive.")
        return settings
