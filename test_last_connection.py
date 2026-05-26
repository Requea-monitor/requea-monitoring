from playwright.sync_api import sync_playwright
import os, json, re, html
from datetime import datetime
from zoneinfo import ZoneInfo

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")

TEST_URL = "https://ccvba.requea.com/do/iotGateway:get?sysId=b3cd0af4867f9026018682d1d5081219&pctx=c34ba508a8d34f1a859a4ba9ff0705ae"


def clean(v):
    return " ".join(
        str(v or "")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace("\xa0", " ")
        .split()
    ).strip()


def parse_date(text):
    text = html.unescape(str(text or ""))
    text = clean(text)

    m = re.search(
        r"Derni[eè]re\s+connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if not m:
        return None

    return datetime.strptime(
        m.group(1),
        "%d/%m/%Y %H:%M:%S"
    ).replace(tzinfo=PARIS)


def login(page, cluster):
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


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context()
    page = context.new_page()

    cluster = next(c for c in CONFIG if "ccvba" in c["url"].lower())

    login(page, cluster)

    page.goto(TEST_URL, wait_until="networkidle", timeout=60000)
    page.wait_for_timeout(5000)

    body = page.locator("body").inner_text()
    source = page.content()

    print("URL finale :", page.url)
    print("---- BODY 1500 premiers caractères ----")
    print(body[:1500])

    date = parse_date(body)

    if not date:
        date = parse_date(source)

    if date:
        print("DATE TROUVÉE :", date.strftime("%d/%m/%Y %H:%M:%S"))
    else:
        print("DATE NON TROUVÉE")

    browser.close()
