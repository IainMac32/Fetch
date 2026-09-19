from flask import Flask, request
import requests

app = Flask(__name__)

LINQ_API_KEY = ""

@app.route("/linq-webhook", methods=["POST"])
def linq_webhook():
    payload = request.json

    parts = payload.get("data", {}).get("parts", [])

    for part in parts:
        if part.get("type") == "text":
            message = part.get("value", "")
            print(f"You texted: {message}")

            if message.lower() == "hello":
                print("Sending a response back to the user...")

                # Message we want to send
                data = {
                    "from": "+16462397830",
                    "to": [
                        "+19058082785"
                    ],
                    "message": {
                        "parts": [
                            {
                                "type": "text",
                                "value": "hello to you too"
                            }
                        ]
                    }
                }

                # Send the message through LINQ
                response = requests.post(
                    "https://api.linqapp.com/v3/messages",
                    headers={
                        "Authorization": f"Bearer {LINQ_API_KEY}",
                        "Content-Type": "application/json"
                    },
                    json=data
                )

                print("LINQ response:", response.status_code)
                print(response.text)

    return "", 200


@app.route("/", methods=["GET"])
def home():
    return "Linq webhook is running!"


if __name__ == "__main__":
    app.run(port=5000)