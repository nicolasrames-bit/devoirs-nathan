#!/usr/bin/env python3
"""
Sync des devoirs Webtop (Smart School) -> page web familiale chiffrée + Google Calendar.

Tourne dans GitHub Actions. Toute la configuration passe par des variables
d'environnement (secrets GitHub) — aucun identifiant dans le code.

Variables :
  WEBTOP_USERNAME, WEBTOP_PASSWORD   (obligatoires)
  FAMILY_PASSWORD                    (obligatoire — mot de passe de la page famille)
  WEBTOP_DATA                        (optionnel — clé "Data" propre à l'école si la connexion échoue)
  WEBTOP_CLASS_CODE, WEBTOP_CLASS_NUMBER (optionnels — sinon détectés automatiquement)
  WEBTOP_STUDENT_NAME                (optionnel — prénom de l'enfant si compte parent multi-enfants)
  GOOGLE_SERVICE_ACCOUNT_JSON        (optionnel — active la sync Google Calendar)
  GOOGLE_CALENDAR_ID                 (optionnel — l'agenda « Devoirs Nathan »)
  SITE_URL                           (fourni par le workflow — sert à relire l'historique déjà publié)
  DIAGNOSTIC=1                       (affiche la STRUCTURE des réponses Webtop, sans aucune valeur)
"""
from __future__ import annotations

import asyncio
import base64
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).parent
OUT = ROOT / "out" / "site"
TZ = dt.timezone(dt.timedelta(hours=2))  # Israël (l'heure exacte importe peu : on ne garde que des dates)
KEEP_PAST_DAYS = 30
PBKDF2_ITER = 250_000

BASE_URL = "https://webtopserver.smartschool.co.il"
DEFAULT_DATA = "+Aabe7FAdVluG6Lu+0ibrA=="


def log(*a: Any) -> None:
    print(*a, flush=True)


def env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()


# --------------------------------------------------------------------------- #
# Diagnostic : structure seulement, jamais de valeurs (les logs d'un repo public
# sont visibles par tous).
# --------------------------------------------------------------------------- #
def skeleton(obj: Any, depth: int = 0, max_depth: int = 4) -> Any:
    if depth > max_depth:
        return "…"
    if isinstance(obj, dict):
        return {k: skeleton(v, depth + 1, max_depth) for k, v in obj.items()}
    if isinstance(obj, list):
        if not obj:
            return "list[0]"
        return {f"list[{len(obj)}] of": skeleton(obj[0], depth + 1, max_depth)}
    return type(obj).__name__


# --------------------------------------------------------------------------- #
# Client Webtop minimal (mêmes endpoints que la lib open-source pywebtop)
# --------------------------------------------------------------------------- #
class Webtop:
    def __init__(self, username: str, password: str, data: str) -> None:
        import httpx

        self.username, self.password, self.data = username, password, data
        relay = env("WEBTOP_RELAY_URL").rstrip("/")
        extra = {"X-Relay-Token": env("WEBTOP_RELAY_TOKEN")} if relay else {}
        log(f"  via {'relais Israël' if relay else 'connexion directe'}")
        self.http = httpx.AsyncClient(
            base_url=relay or BASE_URL,
            timeout=30,
            follow_redirects=True,
            headers={
                "Content-Type": "application/json; charset=utf-8",
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "he-IL,he;q=0.9,en;q=0.8",
                "Origin": "https://webtop.smartschool.co.il",
                "Referer": "https://webtop.smartschool.co.il/",
                "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                               "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"),
                **extra,
            },
        )
        self.login_data: dict = {}

    async def close(self) -> None:
        await self.http.aclose()

    async def post(self, path: str, body: dict) -> dict:
        r = await self.http.post(path, json=body)
        if r.status_code >= 400:
            # Infos techniques seulement (pare-feu, serveur) — jamais le corps d'une réponse réussie
            hint = re.sub(r"<[^>]+>|\s+", " ", r.text)[:240].strip()
            server = r.headers.get("server", "?")
            raise RuntimeError(f"Webtop {path} -> HTTP {r.status_code} (server: {server}) {hint}")
        try:
            return r.json()
        except ValueError:
            raise RuntimeError(f"Webtop {path} -> réponse non JSON")

    def _set_token(self, token: str) -> None:
        self.http.cookies.set("webToken", token)

    async def login(self) -> dict:
        res = await self.post(
            "/server/api/user/LoginByUserNameAndPassword",
            {
                "UserName": self.username,
                "Password": self.password,
                "Data": self.data,
                "RememberMe": False,
                "BiometricLogin": "",
            },
        )
        data = res.get("data") or {}
        if not res.get("status") or not data.get("token"):
            # Uniquement des messages d'erreur / codes, jamais de données personnelles
            info = {k: v for k, v in res.items()
                    if isinstance(v, (str, int, bool)) and not isinstance(v, bool) or k == "status"}
            info = {k: (v[:160] if isinstance(v, str) else v) for k, v in info.items()}
            raise RuntimeError(f"Connexion Webtop échouée. Réponse : {json.dumps(info, ensure_ascii=False)}"
                               f" · clés data : {list(data.keys()) if isinstance(data, dict) else type(data).__name__}")
        self._set_token(data["token"])
        self.login_data = data
        return data

    async def linked_students(self) -> list:
        res = await self.post("/server/api/user/GetMultipleUsersForUser", {})
        return (res.get("data") or []) if res.get("status") else []

    async def switch_student(self, student_id: Any) -> dict:
        res = await self.post(
            "/server/api/user/ChangeUser",
            {"StudentId": student_id, "institutionCode": None, "savedUser": "", "userType": None},
        )
        data = res.get("data") or {}
        if data.get("token"):
            self._set_token(data["token"])
        if data:
            self.login_data = data
        return data

    async def dashboard(self) -> dict:
        return await self.post("/server/api/dashboard/InitDashboard", {})

    async def homework(self, enc_id: str, class_code: int, class_number: int) -> dict:
        return await self.post(
            "/server/api/dashboard/GetHomeWork",
            {"id": enc_id, "ClassCode": class_code, "ClassNumber": class_number},
        )

    async def schedule(self, week_index: int, study_year: int, enc_id: str, class_code: int) -> dict:
        return await self.post(
            "/server/api/PupilCard/GetPupilScheduale",
            {"weekIndex": week_index, "viewType": 0, "studyYear": study_year,
             "studentID": enc_id, "classCode": class_code, "moduleID": 10},
        )


class SessionExpired(RuntimeError):
    pass


class WebtopSession(Webtop):
    """
    Réutilise une session ouverte à la main par le parent (cookie webToken copié dans le secret
    WEBTOP_SESSION). Le script ne se connecte jamais lui-même : la connexion (et son contrôle
    anti-robot) est faite par une personne, dans son navigateur.
    """

    def __init__(self, session: str) -> None:
        super().__init__("", "", "")
        token = session.strip()
        token = re.sub(r"^(cookie:\s*)?.*?webToken=", "", token, flags=re.I) if "webToken=" in token else token
        token = token.split(";")[0].strip()
        self._set_token(token)
        log("  via la session Webtop du parent (secret WEBTOP_SESSION)")

    async def login(self) -> dict:
        res = await self.post("/server/api/PupilCard/GetSettingsList", {})
        data = res.get("data") or {}
        kids = data.get("children") or []
        if not res.get("status") or not kids:
            raise SessionExpired("Session Webtop expirée ou invalide")
        wanted = env("WEBTOP_STUDENT_NAME")
        k = next((c for c in kids if wanted and wanted in f"{c.get('firstName')} {c.get('lastName')}"), kids[0])
        self.login_data = {"id": k.get("id"), "classCode": k.get("classCode"),
                           "classNumber": k.get("classNum"), "firstName": k.get("firstName"),
                           "studyYear": data.get("currentStudyYear")}
        log(f"✓ Session valide ({len(kids)} enfant(s) sur le compte)")
        return self.login_data


class WebtopMoE(Webtop):
    """
    Connexion par l'identification du ministère de l'Éducation (lgn.edu.gov.il), dans un vrai
    navigateur invisible (Edge/Chrome via Playwright). Les appels API sont ensuite faits depuis
    la page Webtop elle-même, avec la session du navigateur.
    Parcours inspiré de la lib open-source webtop-il-kit.
    """

    WEBTOP = "https://webtop.smartschool.co.il"

    def __init__(self, username: str, password: str, data: str) -> None:  # noqa: D401
        self.username, self.password = username, password
        self.login_data: dict = {}
        self.captured: list[tuple[str, Any]] = []
        self.requests: list[tuple[str, Any]] = []
        self.pw = self.browser = self.page = None
        if type(self) is WebtopMoE:
            log("  via identification ministère de l'Éducation (navigateur)")

    async def _start(self) -> None:
        from playwright.async_api import async_playwright

        self.pw = await async_playwright().start()
        last = None
        for kw in ({"channel": "msedge"}, {"channel": "chrome"}, {}):
            try:
                self.browser = await self.pw.chromium.launch(
                    headless=True, args=["--disable-blink-features=AutomationControlled"], **kw)
                break
            except Exception as e:
                last = e
        if not self.browser:
            raise RuntimeError(f"Aucun navigateur utilisable (Edge/Chrome) : {last}")
        ctx = await self.browser.new_context(
            locale="he-IL", timezone_id="Asia/Jerusalem", viewport={"width": 1280, "height": 900},
            user_agent=("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like "
                        "Gecko) Chrome/131.0.0.0 Safari/537.36 Edg/131.0.0.0"),
        )
        await ctx.add_init_script("Object.defineProperty(navigator,'webdriver',{get:()=>undefined})")
        self.page = await ctx.new_page()

        async def grab(resp):
            try:
                if "webtopserver" in resp.url and "json" in (resp.headers.get("content-type") or ""):
                    self.captured.append((resp.url.split("?")[0].split(".co.il")[-1], await resp.json()))
            except Exception:
                pass

        self.page.on("response", lambda r: asyncio.ensure_future(grab(r)))

        def req(r):
            if "webtopserver" in r.url and r.method == "POST":
                try:
                    body = r.post_data_json
                except Exception:
                    body = None
                self.requests.append((r.url.split("?")[0].split(".co.il")[-1], body))

        self.page.on("request", req)

    async def close(self) -> None:
        try:
            if self.browser:
                await self.browser.close()
        finally:
            if self.pw:
                await self.pw.stop()

    async def _set_token(self, token: str) -> None:  # type: ignore[override]
        await self.page.context.add_cookies([
            {"name": "webToken", "value": token, "domain": "webtopserver.smartschool.co.il",
             "path": "/", "secure": True, "sameSite": "None"}])

    async def post(self, path: str, body: dict) -> dict:
        res = await self.page.evaluate(
            """async ([url, body]) => {
                 const r = await fetch(url, {method: 'POST', credentials: 'include',
                   headers: {'Content-Type': 'application/json; charset=utf-8',
                             'Accept': 'application/json, text/plain, */*'},
                   body: JSON.stringify(body)});
                 return {status: r.status, text: await r.text()};
               }""",
            [BASE_URL + path, body],
        )
        if res["status"] >= 400:
            hint = re.sub(r"<[^>]+>|\s+", " ", res["text"])[:200].strip()
            raise RuntimeError(f"Webtop {path} -> HTTP {res['status']} {hint}")
        try:
            return json.loads(res["text"])
        except ValueError:
            raise RuntimeError(f"Webtop {path} -> réponse non JSON")

    async def _settle(self, timeout: int = 15000) -> None:
        """Attend que la page se calme, sans exiger un silence réseau total (le site garde des connexions ouvertes)."""
        try:
            await self.page.wait_for_load_state("load", timeout=timeout)
        except Exception:
            pass
        try:
            await self.page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            pass

    async def _first_visible(self, selectors: list[str], timeout: int = 15000):
        deadline = asyncio.get_event_loop().time() + timeout / 1000
        while asyncio.get_event_loop().time() < deadline:
            for sel in selectors:
                loc = self.page.locator(sel)
                try:
                    n = await loc.count()
                    for i in range(n):
                        if await loc.nth(i).is_visible():
                            return loc.nth(i)
                except Exception:
                    pass
            await asyncio.sleep(0.5)
        return None

    async def _fail(self, step: str) -> None:
        p = self.page
        host = re.sub(r"\?.*", "", p.url) if p else "?"
        title = await p.title() if p else "?"
        body = ""
        try:
            text = await p.inner_text("body")
            # On ne garde que les lignes qui ressemblent à des messages d'erreur
            body = " | ".join(l.strip() for l in text.splitlines()
                              if any(w in l for w in ("שגוי", "שגיאה", "נכשל", "חסום", "לא תקין", "אנא",
                                                      "error", "Error", "invalid")))[:300]
        except Exception:
            pass
        raise RuntimeError(f"Connexion ministère échouée à l'étape « {step} » — page: {host} — "
                           f"titre: {title} — messages: {body or '(aucun)'}")

    async def login(self) -> dict:
        await self._start()
        p = self.page
        await p.goto(self.WEBTOP, wait_until="domcontentloaded", timeout=60000)
        await self._settle()
        if "could not be satisfied" in (await p.title()).lower():
            await self._fail("accès Webtop (bloqué)")

        cookie_btn = await self._first_visible(['button:has-text("אשר cookies")'], 3000)
        if cookie_btn:
            await cookie_btn.click()

        moe = await self._first_visible(['button:has-text("הזדהות משרד החינוך")',
                                         'a:has-text("הזדהות משרד החינוך")',
                                         'button:has-text("משרד החינוך")'], 30000)
        if not moe:
            await self._fail("bouton ministère")
        await moe.click()
        try:
            await p.wait_for_url("**lgn.edu.gov.il/**", timeout=60000)
        except Exception:
            await self._fail("redirection vers lgn.edu.gov.il")
        await self._settle()

        tab = await self._first_visible(
            ['[role="tab"]:has-text("קוד משתמש וסיסמה"):not(:has-text("חד פעמי"))'], 8000)
        if tab:
            await tab.click()
            await asyncio.sleep(1)

        user = await self._first_visible(['input[formcontrolname*="username" i]',
                                          'input[aria-label*="קוד המשתמש"]',
                                          'input[type="text"]:not([readonly])'], 20000)
        pwd = await self._first_visible(['input[type="password"]'], 20000)
        if not user or not pwd:
            await self._fail("formulaire identifiant / mot de passe")
        await user.fill(self.username)
        await pwd.click()
        await pwd.fill(self.password)
        await asyncio.sleep(1.5)

        btn = await self._first_visible(['button[type="submit"]:has-text("כניסה")',
                                         'button:has-text("כניסה"):not([role="tab"])'], 10000)
        if not btn:
            await self._fail("bouton כניסה")
        await btn.click()

        # Retour sur Webtop (hors page de login)
        loop = asyncio.get_event_loop()
        deadline = loop.time() + 90
        while loop.time() < deadline:
            url = p.url.lower()
            if "webtop.smartschool.co.il" in url and "login" not in url:
                break
            await asyncio.sleep(1)
        else:
            await self._fail("retour vers Webtop après connexion")
        await self._settle(20000)
        await asyncio.sleep(3)

        # Données de session : la réponse JSON capturée qui contient un token / un id élève
        for _, js in reversed(self.captured):
            d = js.get("data") if isinstance(js, dict) else None
            if isinstance(d, dict) and ("token" in d or "studentId" in d):
                self.login_data = d
                break
        if self.login_data.get("token"):
            await self._set_token(self.login_data["token"])
        log(f"✓ Connecté via le ministère ({len(self.captured)} réponses Webtop observées)")
        return self.login_data

    async def explore(self, route: str) -> tuple[list, list]:
        """Ouvre une page de Webtop et renvoie (requêtes, réponses) API qu'elle a déclenchées."""
        nr, nc = len(self.requests), len(self.captured)
        await self.page.goto(self.WEBTOP + route, wait_until="domcontentloaded", timeout=60000)
        await self._settle(20000)
        await asyncio.sleep(4)
        return self.requests[nr:], self.captured[nc:]

    async def click_path(self, texts: list[str]) -> tuple[list, list]:
        """Depuis l'accueil, clique successivement sur des liens/boutons par leur texte."""
        nr, nc = len(self.requests), len(self.captured)
        await self.page.goto(self.WEBTOP + "/Dashboard", wait_until="domcontentloaded", timeout=60000)
        await self._settle(20000)
        for t in texts:
            el = await self._first_visible([f'a:has-text("{t}")', f'button:has-text("{t}")',
                                            f'[role="link"]:has-text("{t}")', f'[role="menuitem"]:has-text("{t}")',
                                            f'li:has-text("{t}")', f'span:has-text("{t}")'], 15000)
            if not el:
                log(f"  (lien « {t} » introuvable — page {re.sub(r'[?].*', '', self.page.url)})")
                break
            await el.click()
            await self._settle(20000)
            await asyncio.sleep(4)
        return self.requests[nr:], self.captured[nc:]

    async def switch_student(self, student_id: Any) -> dict:
        res = await self.post(
            "/server/api/user/ChangeUser",
            {"StudentId": student_id, "institutionCode": None, "savedUser": "", "userType": None},
        )
        data = res.get("data") or {}
        if data.get("token"):
            await self._set_token(data["token"])
        if data:
            self.login_data = data
        return data


class WebtopForm(WebtopMoE):
    """Connexion avec l'identifiant Webtop, en remplissant le vrai formulaire du site (le site
    fournit lui-même la clé « Data » propre à l'école)."""

    def __init__(self, username: str, password: str, data: str) -> None:
        super().__init__(username, password, data)
        log("  (formulaire Webtop dans le navigateur)")

    async def login(self) -> dict:
        await self._start()
        p = self.page
        await p.goto(self.WEBTOP, wait_until="domcontentloaded", timeout=60000)
        await self._settle()
        cookie_btn = await self._first_visible(['button:has-text("אשר cookies")'], 3000)
        if cookie_btn:
            await cookie_btn.click()

        user = await self._first_visible(['input[formcontrolname*="user" i]', 'input[name*="user" i]',
                                          'input[id*="user" i]', 'input[type="text"]:not([readonly])'], 30000)
        pwd = await self._first_visible(['input[type="password"]'], 15000)
        if not user or not pwd:
            await self._fail("formulaire Webtop")
        # Diagnostic : structure du formulaire (attributs seulement, aucune valeur)
        form_info = await p.evaluate("""() => ({
            inputs: [...document.querySelectorAll('input')].filter(e => e.offsetParent).map(e =>
                [e.type, e.getAttribute('formcontrolname'), e.name, e.id, e.placeholder].filter(Boolean).join('/')),
            buttons: [...document.querySelectorAll('button')].filter(e => e.offsetParent).map(e =>
                (e.innerText || '').trim().slice(0, 30) + ' [' + (e.type || '') + (e.disabled ? ',disabled' : '') + ']'),
            recaptcha: !!document.querySelector('iframe[src*="recaptcha"], .g-recaptcha')
        })""")
        log(f"  formulaire : {json.dumps(form_info, ensure_ascii=False)}")
        # Frappe « humaine » pour déclencher la validation du formulaire Angular
        await user.focus()
        await user.press_sequentially(self.username, delay=60)
        await pwd.focus()
        await pwd.press_sequentially(self.password, delay=60)
        await asyncio.sleep(1)
        btn = await self._first_visible(['button[type="submit"]:not(:has-text("משרד החינוך"))',
                                         'button:has-text("כניסה"):not(:has-text("משרד החינוך"))'], 10000)
        if not btn:
            await self._fail("bouton de connexion Webtop")
        nreq = len(self.requests)
        allreq: list[str] = []
        p.on("request", lambda r: allreq.append(f"{r.method} {re.sub(r'[?].*', '', r.url)[:90]}")
             if r.resource_type in ("xhr", "fetch", "document") else None)
        await btn.click(force=True)

        loop = asyncio.get_event_loop()
        deadline = loop.time() + 60
        login_res = None
        while loop.time() < deadline:
            login_res = next((js for path, js in reversed(self.captured)
                              if path.endswith("LoginByUserNameAndPassword")), None)
            if login_res is not None:
                break
            await asyncio.sleep(1)
        if login_res is None:
            log(f"  requêtes après le clic : {allreq[:15]}")
            try:
                errs = await p.evaluate("""() => [...document.querySelectorAll(
                    'mat-error, .mat-mdc-form-field-error, snack-bar-container, .mat-mdc-snack-bar-label, [role=alert], .error, .toast-message')]
                    .map(e => (e.innerText || '').trim()).filter(Boolean).slice(0, 5)""")
                log(f"  messages à l'écran : {errs}")
            except Exception:
                pass
            await self._fail("réponse de connexion Webtop")
        req = next((b for path, b in reversed(self.requests) if path.endswith("LoginByUserNameAndPassword")), None)
        if isinstance(req, dict):
            d = req.get("Data")
            log(f"  clé Data du site : {'identique' if d == DEFAULT_DATA else 'différente'} de celle par défaut"
                f" · champs envoyés : {sorted(req.keys())}")
        if not login_res.get("status"):
            msg = login_res.get("errorDescription") or login_res.get("message") or ""
            await asyncio.sleep(2)
            await self._fail(f"identifiant Webtop refusé par le site {('(' + str(msg)[:120] + ')') if msg else ''}")
        self.login_data = login_res.get("data") or {}
        if self.login_data.get("token"):
            await self._set_token(self.login_data["token"])
        await self._settle(20000)
        log("✓ Connecté avec l'identifiant Webtop (formulaire)")
        return self.login_data


def find_key(obj: Any, patterns: tuple[str, ...]) -> Any:
    """Cherche récursivement la première clé dont le nom (minuscule) est dans patterns."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k.lower() in patterns and v not in (None, "", []):
                return v
        for v in obj.values():
            found = find_key(v, patterns)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj:
            found = find_key(v, patterns)
            if found is not None:
                return found
    return None


async def fetch_webtop(diagnostic: bool) -> tuple[list[dict], str, list[dict] | None]:
    # WEBTOP_LOGIN=direct (identifiant Webtop) / moe (ministère) ; vide = essaie direct puis ministère
    mode = env("WEBTOP_LOGIN").lower()
    args = (env("WEBTOP_USERNAME"), env("WEBTOP_PASSWORD"), env("WEBTOP_DATA", DEFAULT_DATA))
    if env("WEBTOP_SESSION"):
        wt: Webtop = WebtopSession(env("WEBTOP_SESSION"))
        mode = "session"
    else:
        wt = WebtopMoE(*args) if mode == "moe" else Webtop(*args)
    try:
        try:
            await wt.login()
        except RuntimeError as e:
            if mode or isinstance(wt, WebtopMoE):
                raise
            log(f"  connexion directe refusée ({str(e)[:60]}) → formulaire Webtop dans le navigateur")
            await wt.close()
            wt = WebtopForm(*args)
            await wt.login()
        log("✓ Connecté à Webtop")
        if diagnostic:
            log("LOGIN data:", json.dumps(skeleton(wt.login_data), ensure_ascii=False, indent=1))
            for path, js in getattr(wt, "captured", []):
                log(f"CAPTURED {path}:", json.dumps(skeleton(js, max_depth=3), ensure_ascii=False))
        captured = [js for _, js in getattr(wt, "captured", [])]

        # Compte parent avec plusieurs enfants -> choisir le bon
        wanted = env("WEBTOP_STUDENT_NAME")
        try:
            kids = await wt.linked_students()
        except Exception:
            kids = []
        if diagnostic:
            log("LINKED students:", json.dumps(skeleton(kids), ensure_ascii=False, indent=1))
        if len(kids) > 1:
            pick = kids[0]
            if wanted:
                for k in kids:
                    if wanted in json.dumps(k, ensure_ascii=False):
                        pick = k
                        break
            sid = pick.get("studentId") or pick.get("id")
            if sid:
                await wt.switch_student(sid)
                log(f"✓ Élève sélectionné ({len(kids)} liés au compte)")

        dash = await wt.dashboard()
        if diagnostic:
            log("DASHBOARD:", json.dumps(skeleton(dash), ensure_ascii=False, indent=1))

        enc_id = wt.login_data.get("id") or wt.login_data.get("studentId")
        cc = env("WEBTOP_CLASS_CODE") or find_key([wt.login_data, dash, *captured], ("classcode",))
        cn = env("WEBTOP_CLASS_NUMBER") or find_key(
            [wt.login_data, dash, *captured], ("classnumber", "classnum")
        )
        log(f"  id élève: {'ok' if enc_id else 'MANQUANT'} · classCode: {'ok' if cc else 'MANQUANT'}"
            f" · classNumber: {'ok' if cn else 'MANQUANT'}")
        if not (enc_id and cc and cn):
            raise RuntimeError(
                "Impossible de trouver id/classCode/classNumber. Lance le workflow en mode "
                "diagnostic et renseigne WEBTOP_CLASS_CODE / WEBTOP_CLASS_NUMBER."
            )

        hw = await wt.homework(enc_id, int(cc), int(cn))
        if diagnostic:
            log("HOMEWORK:", json.dumps(skeleton(hw), ensure_ascii=False, indent=1))
            if isinstance(wt, WebtopMoE):
                await explore_ui(wt, captured)
        if hw.get("status") is False:
            raise RuntimeError(f"GetHomeWork a échoué : {hw.get('errorDescription') or '?'}")

        first = str(wt.login_data.get("firstName") or wanted or "")

        lessons: list[dict] | None = None
        try:
            lessons = await fetch_schedule(wt, enc_id, int(cc), diagnostic)
        except Exception as e:  # l'emploi du temps ne doit jamais bloquer les devoirs
            log(f"  ⚠ Emploi du temps non récupéré : {type(e).__name__}: {e}")
        return normalize(hw), first, lessons
    finally:
        await wt.close()


def payload_shape(body: Any) -> Any:
    """Forme d'un corps de requête : valeurs courtes/numériques/dates visibles, le reste masqué."""
    if isinstance(body, dict):
        return {k: payload_shape(v) for k, v in body.items()}
    if isinstance(body, list):
        return [payload_shape(v) for v in body[:2]]
    if isinstance(body, (int, float, bool)) or body is None:
        return body
    s = str(body)
    if re.fullmatch(r"[\d\-/:T. ]{1,30}", s) or len(s) <= 4:
        return s
    return f"str[{len(s)}]"


async def explore_ui(wt: "WebtopMoE", captured: list) -> None:
    """Diagnostic : liste le menu et observe les appels API des pages devoirs / emploi du temps."""
    for js in captured:
        items = find_key(js, ("menuitems",))
        if items:
            for it in items:
                subs = [(s.get("title"), s.get("moduleFile")) for s in (it.get("subItems") or [])]
                log(f"MENU {it.get('title')} -> {it.get('moduleFile')} {subs if subs else ''}")
            break
    # Essai direct avec l'id renvoyé par GetSettingsList (peut différer de celui du login)
    try:
        st = await wt.post("/server/api/PupilCard/GetSettingsList", {})
        kids = (st.get("data") or {}).get("children") or []
        year = (st.get("data") or {}).get("currentStudyYear")
        log(f"SETTINGS children={len(kids)} currentStudyYear={year}")
        if kids:
            k = kids[0]
            hw = await wt.homework(k["id"], int(k["classCode"]), int(k["classNum"]))
            log("HOMEWORK(settings id):", json.dumps(skeleton(hw, max_depth=5), ensure_ascii=False),
                "| allowToViewThis =", (hw.get("data") or {}).get("allowToViewThis"),
                "| erreur =", hw.get("errorDescription"))
            sc = await wt.schedule(0, int(year or 0), k["id"], int(k["classCode"]))
            log("SCHEDULE(settings id): erreur =", sc.get("errorDescription"))
        dash = await wt.dashboard()
        flags = {k2: v for k2, v in (dash.get("data") or {}).items() if k2.startswith("allow")}
        log("DASHBOARD flags:", flags)
        for ep in ("/server/api/dashboard/ShotefSchedualeDataForToday",
                   "/server/api/dashboard/ChangesAndMessagesDataForToday"):
            try:
                r = await wt.post(ep, {})
                log(f"TRY {ep}:", json.dumps(skeleton(r, max_depth=6), ensure_ascii=False),
                    "| erreur =", r.get("errorDescription"))
            except Exception as e:
                log(f"TRY {ep}: {type(e).__name__} {str(e)[:120]}")
    except Exception as e:
        log(f"SETTINGS erreur {type(e).__name__}: {e}")

    for texts in (["כרטיס תלמיד", "נושאי שיעור ושיעורי-בית"], ["כרטיס תלמיד", "מערכת שעות"],
                  ["מ.שעות ושינויים"]):
        try:
            reqs, resps = await wt.click_path(texts)
        except Exception as e:
            log(f"CLICK {texts}: erreur {type(e).__name__}: {str(e)[:120]}")
            continue
        log(f"CLICK {' > '.join(texts)} -> page {re.sub(r'[?].*', '', wt.page.url)}")
        skip = ("GetYearRange", "SaveStatistics", "GetInitData", "GetMenuCounters", "CheckToken")
        for path, body in reqs:
            if not path.endswith(skip):
                log(f"  REQ {path} {json.dumps(payload_shape(body), ensure_ascii=False)}")
        for path, js in resps:
            if not path.endswith(skip):
                log(f"  RESP {path} {json.dumps(skeleton(js, max_depth=6), ensure_ascii=False)}")


def week_sunday(today: dt.date, week_index: int) -> dt.date:
    return today - dt.timedelta(days=(today.weekday() + 1) % 7) + dt.timedelta(weeks=week_index)


async def fetch_schedule(wt: Webtop, enc_id: str, class_code: int, diagnostic: bool) -> list[dict]:
    today = dt.datetime.now(TZ).date()
    # En Israël l'année scolaire 2026-2027 est souvent notée 2027 : on essaie les deux.
    years = [today.year + 1, today.year] if today.month >= 8 else [today.year, today.year - 1]
    for year in years:
        lessons: list[dict] = []
        for week in (0, 1):
            res = await wt.schedule(week, year, enc_id, class_code)
            if diagnostic and week == 0:
                log(f"SCHEDULE (studyYear={year}):", json.dumps(skeleton(res, max_depth=6),
                                                               ensure_ascii=False, indent=1))
            lessons += normalize_schedule(res, week_sunday(today, week))
        if lessons:
            log(f"✓ Emploi du temps : {len(lessons)} cours (studyYear={year})")
            return dedupe_lessons(lessons)
    log("  ⚠ Emploi du temps vide")
    return []


# --------------------------------------------------------------------------- #
# Normalisation : le format exact de GetHomeWork n'est pas documenté, donc on
# repère les champs par leur nom.
# --------------------------------------------------------------------------- #
DATE_KEYS = ("homeworkdate", "duedate", "todate", "date", "lessondate", "hwdate", "startdate")
SUBJECT_KEYS = ("subjectname", "subject", "professionname", "profession", "lessonname",
                "coursename", "studysubject", "title")
CONTENT_KEYS = ("homework", "homeworktext", "homeworkcontent", "hwtext", "description",
                "content", "combined", "text", "remark", "note", "details")
TEACHER_KEYS = ("teachername", "teacher", "teacherfullname")


def pick(d: dict, keys: tuple[str, ...], want_str: bool = True) -> Any:
    low = {k.lower(): v for k, v in d.items()}
    for k in keys:  # correspondance exacte d'abord
        v = low.get(k)
        if v not in (None, "") and (not want_str or isinstance(v, (str, int, float))):
            return v
    for k in keys:  # puis "contient"
        for lk, v in low.items():
            if k in lk and v not in (None, "") and (not want_str or isinstance(v, (str, int, float))):
                return v
    return None


def parse_date(v: Any) -> dt.date | None:
    if v is None:
        return None
    s = str(v).strip()
    m = re.search(r"/Date\((\d+)", s)
    if m:
        return dt.datetime.fromtimestamp(int(m.group(1)) / 1000, TZ).date()
    m = re.match(r"(\d{4})-(\d{2})-(\d{2})", s)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    m = re.match(r"(\d{1,2})[/.\-](\d{1,2})[/.\-](\d{2,4})", s)
    if m:
        y = int(m.group(3))
        y = y + 2000 if y < 100 else y
        return dt.date(y, int(m.group(2)), int(m.group(1)))
    return None


def clean(s: Any) -> str:
    s = re.sub(r"<br\s*/?>", "\n", str(s or ""), flags=re.I)
    s = re.sub(r"<[^>]+>", "", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\n{3,}", "\n\n", s).strip()


def iter_dicts(obj: Any):
    if isinstance(obj, dict):
        yield obj
        for v in obj.values():
            yield from iter_dicts(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from iter_dicts(v)


def item_id(date: str, subject: str, content: str) -> str:
    return hashlib.sha1(f"{date}|{subject}|{content[:300]}".encode()).hexdigest()[:16]


def normalize(resp: Any) -> list[dict]:
    items: dict[str, dict] = {}
    for d in iter_dicts(resp.get("data", resp) if isinstance(resp, dict) else resp):
        date = parse_date(pick(d, DATE_KEYS))
        content = clean(pick(d, CONTENT_KEYS))
        if not date or not content:
            continue
        subject = clean(pick(d, SUBJECT_KEYS)) or "Devoir"
        if subject == content:
            subject = "Devoir"
        teacher = clean(pick(d, TEACHER_KEYS))
        iso = date.isoformat()
        iid = item_id(iso, subject, content)
        items[iid] = {"id": iid, "date": iso, "subject": subject, "content": content,
                      "teacher": teacher}
    return sorted(items.values(), key=lambda x: (x["date"], x["subject"]))


# --------------------------------------------------------------------------- #
# Emploi du temps : même approche, en héritant du jour/de la date des parents
# (ex. {jours: [{date, heures: [{hour, subject}]}]}).
# --------------------------------------------------------------------------- #
HOUR_KEYS = ("hournum", "lessonhour", "hournumber", "hour", "hourid", "period", "lessonnum",
             "lessonnumber")
DAY_KEYS = ("daynum", "dayofweek", "weekday", "dayindex", "daynumber", "day")
START_KEYS = ("starttime", "fromtime", "begintime", "hourstart", "fromhour", "start")
END_KEYS = ("endtime", "totime", "hourend", "tohour", "end")
ROOM_KEYS = ("roomname", "room", "classroom", "location")
NOTE_KEYS = ("changedescription", "changes", "change", "remark", "note", "comment", "message")
HEB_DAYS = {"ראשון": 0, "שני": 1, "שלישי": 2, "רביעי": 3, "חמישי": 4, "שישי": 5,
            "א": 0, "ב": 1, "ג": 2, "ד": 3, "ה": 4, "ו": 5}


def as_int(v: Any) -> int | None:
    if isinstance(v, bool) or v is None:
        return None
    m = re.search(r"\d+", str(v))
    return int(m.group()) if m else None


def as_time(v: Any) -> str:
    m = re.search(r"(\d{1,2}):(\d{2})", str(v or ""))
    return f"{int(m.group(1)):02d}:{m.group(2)}" if m else ""


def heb_day(v: Any) -> int | None:
    s = re.sub(r"[\"'׳״]|יום", "", str(v)).strip()
    return HEB_DAYS.get(s)


def normalize_schedule(resp: Any, sunday: dt.date) -> list[dict]:
    raw: list[dict] = []

    def walk(obj: Any, ctx: dict) -> None:
        if isinstance(obj, list):
            for v in obj:
                walk(v, ctx)
            return
        if not isinstance(obj, dict):
            return
        ctx = dict(ctx)
        date = parse_date(pick(obj, ("date", "lessondate", "daydate", "fulldate")))
        if date:
            ctx["date"] = date
        day_v = pick(obj, DAY_KEYS)
        if day_v is not None:
            d = heb_day(day_v)
            ctx["day"] = d if d is not None else as_int(day_v)
            ctx["day_heb"] = d is not None
        subject = clean(pick(obj, SUBJECT_KEYS))
        hour = as_int(pick(obj, HOUR_KEYS))
        start = as_time(pick(obj, START_KEYS))
        if subject and (hour is not None or start):
            low = {k.lower(): v for k, v in obj.items()}
            cancelled = any("cancel" in k and v not in (None, False, 0, "", "0") for k, v in low.items())
            note = clean(pick(obj, NOTE_KEYS))
            if "ביטול" in note or "בוטל" in note:
                cancelled = True
            raw.append({
                "date": ctx.get("date"), "day": ctx.get("day"), "day_heb": ctx.get("day_heb"),
                "hour": hour, "start": start, "end": as_time(pick(obj, END_KEYS)),
                "subject": subject, "teacher": clean(pick(obj, TEACHER_KEYS)),
                "room": clean(pick(obj, ROOM_KEYS)), "cancelled": cancelled,
                "note": "" if note == subject else note,
            })
        for v in obj.values():
            if isinstance(v, (list, dict)):
                walk(v, ctx)

    walk(resp.get("data", resp) if isinstance(resp, dict) else resp, {})

    # Numéros de jour : 0 = dimanche si on voit un 0, sinon 1 = dimanche (convention israélienne)
    nums = [r["day"] for r in raw if r["day"] is not None and not r["day_heb"]]
    zero_based = bool(nums) and min(nums) == 0
    out = []
    for r in raw:
        date = r["date"]
        if date is None and r["day"] is not None:
            idx = r["day"] if (r["day_heb"] or zero_based) else r["day"] - 1
            if 0 <= idx <= 6:
                date = sunday + dt.timedelta(days=idx)
        if date is None:
            continue
        out.append({k: r[k] for k in ("hour", "start", "end", "subject", "teacher", "room",
                                      "cancelled", "note")} | {"date": date.isoformat()})
    return out


def dedupe_lessons(lessons: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for l in lessons:
        seen.setdefault(f"{l['date']}|{l['hour']}|{l['start']}|{l['subject']}|{l['teacher']}", l)
    return sorted(seen.values(), key=lambda l: (l["date"], l["hour"] or 0, l["start"]))


# --------------------------------------------------------------------------- #
# Fusion avec l'historique déjà publié
# --------------------------------------------------------------------------- #
def merge(previous: list[dict], fresh: list[dict], today: dt.date) -> list[dict]:
    cutoff = (today - dt.timedelta(days=KEEP_PAST_DAYS)).isoformat()
    fresh_dates = {i["date"] for i in fresh}
    out = {i["id"]: i for i in fresh}
    for i in previous:
        if i["date"] < cutoff or i["id"] in out:
            continue
        # Pour une date que Webtop vient de renvoyer, Webtop fait foi (devoir modifié/supprimé)
        if i["date"] in fresh_dates and i["date"] >= today.isoformat():
            continue
        out[i["id"]] = i
    return sorted(out.values(), key=lambda x: (x["date"], x["subject"]))


# --------------------------------------------------------------------------- #
# Chiffrement (PBKDF2-SHA256 + AES-GCM), compatible WebCrypto dans le navigateur
# --------------------------------------------------------------------------- #
def _key(password: str, salt: bytes, iterations: int) -> bytes:
    return hashlib.pbkdf2_hmac("sha256", password.encode(), salt, iterations, dklen=32)


def encrypt(payload: dict, password: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    salt, iv = os.urandom(16), os.urandom(12)
    ct = AESGCM(_key(password, salt, PBKDF2_ITER)).encrypt(
        iv, json.dumps(payload, ensure_ascii=False).encode(), None
    )
    b64 = lambda b: base64.b64encode(b).decode()
    return {"v": 1, "iter": PBKDF2_ITER, "salt": b64(salt), "iv": b64(iv), "ct": b64(ct)}


def decrypt(blob: dict, password: str) -> dict:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    d = lambda s: base64.b64decode(s)
    pt = AESGCM(_key(password, d(blob["salt"]), int(blob["iter"]))).decrypt(
        d(blob["iv"]), d(blob["ct"]), None
    )
    return json.loads(pt)


def load_previous(password: str) -> dict:
    url = env("SITE_URL")
    if not url:
        return {}
    try:
        import httpx

        r = httpx.get(url.rstrip("/") + "/data.enc.json", timeout=20, follow_redirects=True)
        if r.status_code != 200:
            return {}
        return decrypt(r.json(), password)
    except Exception as e:  # premier lancement, mot de passe changé, etc.
        log(f"  (historique non relu : {type(e).__name__})")
        return {}


# --------------------------------------------------------------------------- #
# Google Calendar (compte de service)
# --------------------------------------------------------------------------- #
def sync_calendar(items: list[dict], today: dt.date, student: str) -> None:
    sa_json, cal_id = env("GOOGLE_SERVICE_ACCOUNT_JSON"), env("GOOGLE_CALENDAR_ID")
    if not (sa_json and cal_id):
        log("• Google Calendar non configuré — étape ignorée")
        return
    from google.oauth2 import service_account
    from googleapiclient.discovery import build

    creds = service_account.Credentials.from_service_account_info(
        json.loads(sa_json), scopes=["https://www.googleapis.com/auth/calendar"]
    )
    svc = build("calendar", "v3", credentials=creds, cache_discovery=False)

    start = today - dt.timedelta(days=7)
    wanted = {i["id"]: i for i in items if i["date"] >= start.isoformat()}

    existing: dict[str, dict] = {}
    page = None
    while True:
        res = svc.events().list(
            calendarId=cal_id,
            privateExtendedProperty="source=webtop",
            timeMin=dt.datetime.combine(start, dt.time(), TZ).isoformat(),
            singleEvents=True,
            maxResults=250,
            pageToken=page,
        ).execute()
        for ev in res.get("items", []):
            wid = ev.get("extendedProperties", {}).get("private", {}).get("webtopId")
            if wid:
                existing[wid] = ev
        page = res.get("nextPageToken")
        if not page:
            break

    who = f" ({student})" if student else ""
    added = removed = 0
    for wid, it in wanted.items():
        if wid in existing:
            continue
        d = dt.date.fromisoformat(it["date"])
        desc = it["content"] + (f"\n\n👩‍🏫 {it['teacher']}" if it["teacher"] else "")
        svc.events().insert(
            calendarId=cal_id,
            body={
                "summary": f"📚 {it['subject']}{who}",
                "description": desc,
                "start": {"date": d.isoformat()},
                "end": {"date": (d + dt.timedelta(days=1)).isoformat()},
                "transparency": "transparent",
                "reminders": {"useDefault": True},
                "extendedProperties": {"private": {"source": "webtop", "webtopId": wid}},
            },
        ).execute()
        added += 1

    # Supprimer seulement les devoirs à venir qui ont disparu de Webtop
    for wid, ev in existing.items():
        ev_date = ev.get("start", {}).get("date", "")
        if wid not in wanted and ev_date >= today.isoformat():
            svc.events().delete(calendarId=cal_id, eventId=ev["id"]).execute()
            removed += 1
    log(f"✓ Google Calendar : +{added} / -{removed}")


# --------------------------------------------------------------------------- #
def build_site(items: list[dict], lessons: list[dict], student: str, password: str,
               warning: str = "", updated: str = "") -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for f in (ROOT / "site").iterdir():
        if f.is_file():
            shutil.copy(f, OUT / f.name)
    payload = {
        "updated": updated or dt.datetime.now(TZ).isoformat(timespec="minutes"),
        "student": student,
        "items": items,
        "lessons": lessons,
    }
    if warning:
        payload["warning"] = warning
    (OUT / "data.enc.json").write_text(json.dumps(encrypt(payload, password)))
    (OUT / ".nojekyll").write_text("")
    log(f"✓ Portail généré ({len(items)} devoirs, {len(lessons)} cours)")


async def main() -> int:
    family_pw = env("FAMILY_PASSWORD")
    needed = ("FAMILY_PASSWORD",) if env("WEBTOP_SESSION") else ("WEBTOP_USERNAME", "WEBTOP_PASSWORD", "FAMILY_PASSWORD")
    missing = [n for n in needed if not env(n)]
    if missing:
        log("Secrets manquants :", ", ".join(missing))
        return 1

    diagnostic = env("DIAGNOSTIC") in ("1", "true", "yes")
    today = dt.datetime.now(TZ).date()
    prev_payload = load_previous(family_pw)
    previous = prev_payload.get("items", [])
    try:
        fresh, student, lessons = await fetch_webtop(diagnostic)
    except SessionExpired:
        log("⚠ Session Webtop expirée : on republie les dernières données avec un avertissement")
        build_site(prev_payload.get("items", []), prev_payload.get("lessons", []),
                   prev_payload.get("student", ""), family_pw, warning="session_expired",
                   updated=prev_payload.get("updated", ""))
        return 0
    log(f"✓ {len(fresh)} devoirs reçus de Webtop")

    if not fresh and any(i["date"] >= today.isoformat() for i in previous):
        log("  Webtop n'a rien renvoyé : on garde les devoirs déjà connus")
        items = previous
    else:
        items = merge(previous, fresh, today)

    if not lessons:  # échec ou vide : on garde l'emploi du temps déjà publié
        lessons = prev_payload.get("lessons", [])

    build_site(items, lessons, student, family_pw)
    sync_calendar(items, today, student)
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
