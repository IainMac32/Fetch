import os
from dataclasses import dataclass

from dotenv import load_dotenv


@dataclass(frozen=True)
class Settings:
    browserbase_api_key: str = ""
    linq_api_key: str = ""
    linq_webhook_secret: str = ""
    linq_base_url: str = "https://api.linqapp.com/api/partner/v3"
    demo_user_handle: str = ""
    openai_api_key: str = ""
    openai_model: str = "gpt-4.1-mini"

    @classmethod
    def from_env(cls, *, messaging=True):
        load_dotenv()
        required = ["BROWSERBASE_API_KEY"]
        if messaging:
            required += ["LINQ_API_KEY", "LINQ_WEBHOOK_SECRET", "DEMO_USER_HANDLE"]
        missing = [name for name in required if not os.getenv(name)]
        if missing:
            raise ValueError("Set these values in .env: " + ", ".join(missing))
        settings = cls(
            browserbase_api_key=os.getenv("BROWSERBASE_API_KEY", ""),
            linq_api_key=os.getenv("LINQ_API_KEY", ""),
            linq_webhook_secret=os.getenv("LINQ_WEBHOOK_SECRET", ""),
            linq_base_url=os.getenv("LINQ_BASE_URL", cls.linq_base_url).rstrip("/"),
            demo_user_handle=os.getenv("DEMO_USER_HANDLE", ""),
            openai_api_key=os.getenv("OPENAI_API_KEY", ""),
            openai_model=os.getenv("OPENAI_MODEL", "").strip() or cls.openai_model,
        )
        return settings
