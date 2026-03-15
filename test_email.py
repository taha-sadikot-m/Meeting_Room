URL = "https://script.google.com/macros/s/AKfycbzsLTO3y2cLgMViwjZ1aHuUpXtV5fOIbxDLOG75_WofwyC9lOh4x-8z3iBkQNRXzDJt/exec"
DEPLOYMENT_ID = "AKfycbzsLTO3y2cLgMViwjZ1aHuUpXtV5fOIbxDLOG75_WofwyC9lOh4x-8z3iBkQNRXzDJt"


import requests

# Replace with your actual Google Apps Script Web App URL
url = URL

# The data to be sent as a JSON body
payload = {
    "to": "taha.sadikot.m@gmail.com",
    "subject": "Hello",
    "body": "This is a test email sent via API."
}

try:
    # Use the json= parameter to automatically set 'Content-Type: application/json'
    # and encode the dictionary as a JSON string.
    response = requests.post(url, json=payload)
    
    # Check for successful request (status code 200)
    if response.status_code == 200:
        print("Success!")
        print(response.json())  # Print the script's JSON response
    else:
        print(f"Failed with status code: {response.status_code}")
        print(response.text)

except Exception as e:
    print(f"An error occurred: {e}")
