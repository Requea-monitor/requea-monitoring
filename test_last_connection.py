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


def click_correct_gateway_link(row):
    links = row.locator("a")
    clicked = False

    for j in range(links.count()):
        link = links.nth(j)

        href = link.get_attribute("href") or ""
        txt = clean(link.inner_text())

        print("LIEN", j, "TEXT=", txt, "HREF=", href)

        if (
            GATEWAY_ID in href
            or "iotGateway:get" in href
            or "iotGateway" in href
            or GATEWAY_ID in txt
        ):
            link.click()
            clicked = True
            break

    if not clicked:
        print("AUCUN LIEN DETAIL TROUVE, DOUBLE CLIC SUR LA LIGNE")
        row.dblclick()

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

    found_row = False

    for page_index in range(10):
        print("PAGE LISTING", page_index + 1)

        rows = page.locator("tr")
        count = rows.count()

        for i in range(count):
            row = rows.nth(i)
            raw = clean(row.inner_text())

            if GATEWAY_ID not in raw:
                continue

            found_row = True

            print("LIGNE TROUVEE:")
            print(raw[:1000])

            click_correct_gateway_link(row)

            page.wait_for_timeout(12000)

            body = page.locator("body").inner_text()
            html = page.content()

            print("URL APRES CLIC:", page.url)
            print("BODY APRES CLIC:")
            print(clean(body)[:4000])

            date = find_date(body) or find_date(html)

            if date:
                print("DATE TROUVEE:", date)
            else:
                print("DATE NON TROUVEE APRES CLIC")

            os.makedirs("public", exist_ok=True)

            with open("public/index.html", "w", encoding="utf-8") as f:
                f.write("<h1>TEST OK</h1>")

            browser.close()
            raise SystemExit

        if not click_next(page):
            break

    if not found_row:
        print("LIGNE NON TROUVEE POUR", GATEWAY_ID)

    os.makedirs("public", exist_ok=True)

    with open("public/index.html", "w", encoding="utf-8") as f:
        f.write("<h1>TEST OK</h1>")

    browser.close()
