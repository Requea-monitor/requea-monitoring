from playwright.sync_api import sync_playwright
import os
import json
import re
from datetime import datetime
from zoneinfo import ZoneInfo

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

GATEWAY_ID = "00000008004CF990"
PARIS = ZoneInfo("Europe/Paris")


def clean(v):
    return " ".join(str(v or "").replace("\xa0", " ").split())


def parse_date(text):
    text = clean(text)

    # Format français : Dernière connexion: 26/05/2026 17:38:52
    m = re.search(
        r"Derni[eè]re\s+connexion\s*:?\s*"
        r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if m:
        return datetime.strptime(
            m.group(1),
            "%d/%m/%Y %H:%M:%S"
        ).replace(tzinfo=PARIS)

    # Format anglais Requea : Last connection: 5/26/2026, 5:38:52 PM
    m = re.search(
        r"Last\s+connection\s*:?\s*"
        r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4},\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s+[AP]M)",
        text,
        re.I
    )

    if m:
        return datetime.strptime(
            m.group(1),
            "%m/%d/%Y, %I:%M:%S %p"
        ).replace(tzinfo=PARIS)

    return None


def click_next(page):
    clicked = page.evaluate("""
() => {
    const els = Array.from(document.querySelectorAll("a,button,span,div"));

    for (const el of els) {
        const txt = (el.innerText || el.textContent || "").trim().toLowerCase();
        const cls = (el.className || "").toString().toLowerCase();

        if (cls.includes("disabled")) continue;

        if (
            txt === ">" ||
            txt === "›" ||
            txt === "suivant" ||
            txt === "next" ||
            cls.includes("next")
        ) {
            el.click();
            return true;
        }
    }

    return false;
}
""")

    if clicked:
        page.wait_for_timeout(7000)

    return clicked


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    context = browser.new_context(
        viewport={
            "width": 1600,
            "height": 1000
        }
    )

    page = context.new_page()

    cluster = next(
        c for c in CONFIG
        if "ccvba" in c["url"].lower()
    )

    page.goto(
        cluster["url"],
        wait_until="domcontentloaded",
        timeout=60000
    )

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

    found = False

    for page_index in range(10):

        print("PAGE LISTING", page_index + 1)

        try:
            target = page.get_by_text(GATEWAY_ID, exact=True).first
            target.click()
            found = True
        except Exception:
            found = False

        if found:
            page.wait_for_timeout(12000)

            body = page.locator("body").inner_text()

            print("URL APRES CLIC:", page.url)
            print("BODY APRES CLIC:")
            print(clean(body)[:4000])

            dt = parse_date(body)

            if dt:
                print(
                    "DATE TROUVEE:",
                    dt.strftime("%d/%m/%Y %H:%M:%S")
                )
            else:
                print("DATE NON TROUVEE")

            break

        if not click_next(page):
            break

    if not found:
        print("PASSERELLE NON TROUVEE:", GATEWAY_ID)

    os.makedirs("public", exist_ok=True)

    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write("<h1>TEST OK</h1>")

    browser.close()
