from playwright.sync_api import sync_playwright
import os, json, re, html

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

URL = "https://ccvba.requea.com/do/iotGateway:get?sysId=b3cd0af4867f9026018682d1d5081219&pctx=c34ba508a8d34f1a859a4ba9ff0705ae"


def clean(v):
    return " ".join(str(v or "").split())


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    context = browser.new_context()
    page = context.new_page()

    cluster = next(c for c in CONFIG if "ccvba" in c["url"])

    page.goto(cluster["url"])

    page.wait_for_timeout(3000)

    page.locator(
        'input:visible:not([type="password"])'
    ).first.fill(cluster["login"])

    page.locator(
        'input[type="password"]'
    ).first.fill(cluster["password"])

    page.locator(
        'input[type="password"]'
    ).first.press("Enter")

    page.wait_for_timeout(8000)

    print("OUVERTURE DETAIL")

    page.goto(URL, wait_until="networkidle")

    page.wait_for_timeout(10000)

    text = clean(page.locator("body").inner_text())

    print(text)

    m = re.search(
        r"Derni[eè]re connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if m:
        print("DATE TROUVÉE :", m.group(1))
    else:
        print("DATE NON TROUVÉE")

    os.makedirs("public", exist_ok=True)

    with open("public/index.html", "w") as f:
        f.write("<h1>TEST OK</h1>")

    browser.close()
