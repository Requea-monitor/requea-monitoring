from playwright.sync_api import sync_playwright
import os, json, re

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

URL = "https://ccvba.requea.com/do/iotGateway:get?sysId=b3cd0af4867f9026018682d1d5081219&pctx=c34ba508a8d34f1a859a4ba9ff0705ae"

date_regex = re.compile(
    r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})"
)

found = []


def scan_response(response):
    try:
        txt = response.text()
    except Exception:
        return

    if "Dernière connexion" in txt or "Derniere connexion" in txt or date_regex.search(txt):
        print("=== REPONSE SUSPECTE ===")
        print("URL:", response.url)
        print(txt[:3000])

        m = date_regex.search(txt)
        if m:
            print("DATE TROUVÉE:", m.group(1))
            found.append(m.group(1))


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    page.on("response", scan_response)

    cluster = next(c for c in CONFIG if "ccvba" in c["url"].lower())

    page.goto(cluster["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    page.locator('input:visible:not([type="password"]):not([type="hidden"])').first.fill(cluster["login"])
    page.locator('input[type="password"]:visible').first.fill(cluster["password"])
    page.locator('input[type="password"]:visible').first.press("Enter")

    page.wait_for_timeout(8000)

    page.goto(URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(15000)

    if found:
        print("RESULTAT FINAL DATE:", found[0])
    else:
        print("AUCUNE DATE TROUVÉE DANS LES RÉPONSES RÉSEAU")

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w") as f:
        f.write("<h1>TEST OK</h1>")

    browser.close()
