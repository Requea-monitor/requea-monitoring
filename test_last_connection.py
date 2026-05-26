from playwright.sync_api import sync_playwright
import os
import json
import re

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

GATEWAY_ID = "00000008004C744F"

date_regex = re.compile(
    r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})"
)


def clean(v):
    return " ".join(str(v or "").replace("\xa0", " ").split())


def find_date(text):
    text = clean(text)

    m = re.search(
        r"Derni[eè]re\s+connexion\s*:?\s*"
        r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if m:
        return m.group(1)

    m = date_regex.search(text)
    return m.group(1) if m else None


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()

    cluster = next(c for c in CONFIG if "ccvba" in c["url"].lower())

    page.goto(cluster["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    page.locator(
        'input:visible:not([type="password"]):not([type="hidden"])'
    ).first.fill(cluster["login"])

    page.locator(
        'input[type="password"]:visible'
    ).first.fill(cluster["password"])

    page.locator(
        'input[type="password"]:visible'
    ).first.press("Enter")

    page.wait_for_timeout(8000)

    page.goto(
        f'{cluster["url"]}/page/Network_Gateways',
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(12000)

    print("CLIC SUR TEXTE PASSERELLE:", GATEWAY_ID)

    target = page.get_by_text(GATEWAY_ID, exact=True).first
    target.click()

    page.wait_for_timeout(12000)

    body = page.locator("body").inner_text()
    html = page.content()

    print("URL APRES CLIC:", page.url)
    print("BODY APRES CLIC:")
    print(clean(body)[:5000])

    date = find_date(body) or find_date(html)

    if date:
        print("DATE TROUVEE:", date)
    else:
        print("DATE NON TROUVEE APRES CLIC TEXTE")

    os.makedirs("public", exist_ok=True)
    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write("<h1>TEST OK</h1>")

    browser.close()
