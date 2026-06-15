import requests

MEDIA_ID = "c3187fc3-69e6-4947-ad32-44ebb1d9e0ac"
PROJECT_ID = "95902df5-9bb3-48e8-8719-41fbd68a5d37"
DISPLAY_NAME = "Lại đổi tên"

resp = requests.patch(
    "http://127.0.0.1:8100/api/flow/change-displayname",
    json={"media_id": MEDIA_ID, "project_id": PROJECT_ID, "display_name": DISPLAY_NAME},
)
print(f"Status: {resp.status_code}")
print(resp.json())
