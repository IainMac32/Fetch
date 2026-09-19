"""Explicit manual messaging utility. Importing this module never sends a text."""
import argparse
import os

from dotenv import load_dotenv
from shopper.linq import LinqClient

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Send a message to an existing LINQ chat")
    parser.add_argument("chat_id")
    parser.add_argument("message")
    args = parser.parse_args()
    load_dotenv()
    client = LinqClient(os.environ["LINQ_API_KEY"], os.getenv("LINQ_BASE_URL", "https://api.linqapp.com/api/partner/v3"))
    client.send(args.chat_id, args.message)
    print("Message accepted by LINQ.")
