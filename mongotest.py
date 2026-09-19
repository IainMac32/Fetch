from pymongo import MongoClient
from pymongo.server_api import ServerApi
from datetime import datetime, timezone
import os

uri = "mongodb+srv://iainhmacdonald_db_user:oOcXTTeobAdpM1EF@cluster0.ykdex3l.mongodb.net/?appName=Cluster0"  # store the full connection string as an env variable

client = MongoClient(uri, server_api=ServerApi('1'))

try:
    client.admin.command('ping')
    print("Pinged your deployment. You successfully connected to MongoDB!")
except Exception as e:
    print(e)

db = client["myAppDB"]
users = db["users"]
users.create_index("PhoneNumber", unique=True)

def save_user(phone_number, doordash_username, doordash_password, doordash_number):
    result = users.update_one(
        {"PhoneNumber": phone_number},
        {
            "$set": {
                "PhoneNumber": phone_number,
                "doordashusername": doordash_username,
                "doordashpassword": doordash_password,
                "doordashnumber": doordash_number,
                "updatedAt": datetime.now(timezone.utc),
            }
        },
        upsert=True,
    )
    if result.upserted_id:
        print(f"New user created with id: {result.upserted_id}")
    else:
        print("Existing user updated")

save_user("5195551234", "myDoorDashUser", "myDoorDashPass", "5195551234")

def get_doordash_creds(phone_number):
    user = users.find_one({"PhoneNumber": phone_number})
    
    if user is None:
        print("No user found with that phone number.")
        return None
    
    username = user.get("doordashusername")
    password = user.get("doordashpassword")
    
    if username and password:
        print("DoorDash credentials found.")
        return {"doordashusername": username, "doordashpassword": password}
    else:
        print("User exists but DoorDash credentials are missing.")
        return None

# Example usage
creds = get_doordash_creds("5195551234")
if creds:
    print(creds)