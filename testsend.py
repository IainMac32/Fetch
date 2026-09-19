import requests

def send_text(message):
    url = "https://api.linqapp.com/v3/messages"

    headers = {
        "Authorization": "Bearer ",
        "Content-Type": "application/json"
    }

    data = {
        "from": "+16462397830",
        "to": ["+19058082785"],
        "message": {
            "parts": [
                {
                    "type": "text",
                    "value": message
                }
            ]
        }
    }

    response = requests.post(url, headers=headers, json=data)

    print(response.status_code)
    print(response.text)

send_text("Hello World")