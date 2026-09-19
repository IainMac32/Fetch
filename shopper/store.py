import time

from cryptography.fernet import Fernet, InvalidToken
from pymongo import MongoClient
from pymongo.errors import DuplicateKeyError


class Store:
    """Persist encrypted DoorDash credentials and identity-to-context mappings in MongoDB."""

    DATABASE_NAME = "messageshopper"

    def __init__(self, uri, credential_key=None, *, client=None):
        self.client = client or MongoClient(uri, serverSelectionTimeoutMS=5000)
        self.db = self.client[self.DATABASE_NAME]
        self.db.contexts.create_index("user_key", unique=True)
        self.db.events.create_index("event_id", unique=True)
        self.db.events.create_index("received_at", expireAfterSeconds=86400)
        self.db.doordash_credentials.create_index("user_key", unique=True)
        try:
            self.credential_cipher = Fernet(credential_key) if credential_key else None
        except (TypeError, ValueError) as exc:
            raise ValueError("DOORDASH_CREDENTIAL_KEY must be a valid Fernet key") from exc

    def save_credentials(self, user_key, login, password):
        cipher = self._cipher()
        self.db.doordash_credentials.replace_one(
            {"user_key": user_key},
            {"user_key": user_key,
             "login_ciphertext": cipher.encrypt(login.encode("utf-8")),
             "password_ciphertext": cipher.encrypt(password.encode("utf-8")),
             "updated_at": time.time()},
            upsert=True,
        )

    def credentials_for(self, user_key):
        cipher = self._cipher()
        row = self.db.doordash_credentials.find_one(
            {"user_key": user_key}, {"login_ciphertext": 1, "password_ciphertext": 1}
        )
        if row is None:
            return None
        try:
            return tuple(cipher.decrypt(row[field]).decode("utf-8")
                         for field in ("login_ciphertext", "password_ciphertext"))
        except (InvalidToken, UnicodeDecodeError) as exc:
            raise RuntimeError("Stored DoorDash credentials cannot be decrypted; check DOORDASH_CREDENTIAL_KEY") from exc

    def forget_credentials(self, user_key):
        self.db.doordash_credentials.delete_one({"user_key": user_key})

    def _cipher(self):
        if self.credential_cipher is None:
            raise RuntimeError("Set DOORDASH_CREDENTIAL_KEY before saving or using DoorDash credentials")
        return self.credential_cipher

    def context_for(self, user_key):
        row = self.db.contexts.find_one({"user_key": user_key}, {"context_id": 1, "reusable_at": 1})
        return (row["context_id"], row.get("reusable_at", 0)) if row else None

    def save_context(self, user_key, context_id):
        self.db.contexts.update_one(
            {"user_key": user_key},
            {"$set": {"context_id": context_id, "reusable_at": 0}},
            upsert=True,
        )

    def defer_context(self, user_key, until):
        self.db.contexts.update_one({"user_key": user_key}, {"$set": {"reusable_at": until}})

    def claim_event(self, event_id):
        now = time.time()
        try:
            self.db.events.insert_one({"event_id": event_id, "received_at": now})
            return True
        except DuplicateKeyError:
            return False

    def forget_event(self, event_id):
        self.db.events.delete_one({"event_id": event_id})

    def close(self):
        self.client.close()
