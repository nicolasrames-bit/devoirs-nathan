"""
Relais Webtop — Google Cloud Functions (région me-west1 / Tel Aviv).

Webtop refuse les connexions hors d'Israël ; GitHub Actions tourne aux États-Unis.
Ce relais transmet UNIQUEMENT les appels POST /server/api/... vers Webtop, et seulement
si le jeton secret X-Relay-Token est correct. Il ne stocke ni ne journalise rien.
"""
import hmac
import os

import functions_framework
import requests
from flask import Response

UPSTREAM = "https://webtopserver.smartschool.co.il"
BROWSER_HEADERS = {
    "Content-Type": "application/json; charset=utf-8",
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
    "Origin": "https://webtop.smartschool.co.il",
    "Referer": "https://webtop.smartschool.co.il/",
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                   "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
}


@functions_framework.http
def relay(request):
    token = os.environ.get("RELAY_TOKEN", "")
    if not token or not hmac.compare_digest(request.headers.get("X-Relay-Token", ""), token):
        return ("forbidden", 403)
    i = request.path.find("/server/api/")
    if request.method != "POST" or i < 0:
        return ("not found", 404)

    headers = dict(BROWSER_HEADERS)
    if request.headers.get("Cookie"):
        headers["Cookie"] = request.headers["Cookie"]
    r = requests.post(UPSTREAM + request.path[i:], data=request.get_data(),
                      headers=headers, timeout=30)
    return Response(r.content, status=r.status_code,
                    content_type=r.headers.get("content-type", "application/json"))
