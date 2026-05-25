from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import json
import html
import re

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)
HISTORY_FILE = "history.json"

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
except Exception:
    history = {}

gateways = []


def esc(v):
    return html.escape(str(v or ""))


def clean(v):
    return " ".join(
        str(v or "")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace("\xa0", " ")
        .split()
    ).strip()


def strip_tags(v):
    v = html.unescape(str(v or ""))
    v = re.sub(r"<script.*?</script>", " ", v, flags=re.I | re.S)
    v = re.sub(r"<style.*?</style>", " ", v, flags=re.I | re.S)
    v = re.sub(r"<[^>]+>", " ", v)
    return clean(v)


def fmt_date(v):
    if not v:
        return "-"

    try:
        return datetime.fromisoformat(v).astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(v)


def normalize_connection(v):
    t = str(v or "").lower()

    if (
        "déconnect" in t
        or "deconnect" in t
        or "closed" in t
        or "offline" in t
        or "down" in t
    ):
        return "Déconnectée", True

    if (
        "connectée" in t
        or "connectee" in t
        or "connected" in t
        or "online" in t
    ):
        return "Connectée", False

    return clean(v) or "Inconnue", True


def parse_last_connection(text):
    text = clean(text)

    patterns = [
        r"Derni[eè]re\s+connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Derniere\s+connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Last\s+connection\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            try:
                return datetime.strptime(
                    match.group(1),
                    "%d/%m/%Y %H:%M:%S"
                ).replace(tzinfo=PARIS)
            except Exception:
                return None

    return None


def geoloc_from_text(text):
    text = clean(text)

    patterns = [
        r"([0-9]{2}\.[0-9]+)\s*,\s*([0-9]{1,2}\.[0-9]+)",
        r"([0-9]{2}\.[0-9]+)\s+([0-9]{1,2}\.[0-9]+)",
        r"lat(?:itude)?\s*:?\s*([0-9]{2}\.[0-9]+).*?lon(?:gitude)?\s*:?\s*([0-9]{1,2}\.[0-9]+)",
    ]

    for pattern in patterns:
        match = re.search(pattern, text, re.I)
        if match:
            return f"{match.group(1)}, {match.group(2)}"

    return ""


def make_absolute_url(base_url, url):
    if not url:
        return ""

    url = html.unescape(url)

    if url.startswith("http"):
        return url

    if url.startswith("/"):
        return base_url.rstrip("/") + url

    return base_url.rstrip("/") + "/" + url.lstrip("/")


def login(page, cluster):
    page.goto(cluster["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(4000)

    username = page.locator(
        'input:visible:not([type="password"]):not([type="hidden"])'
    ).first

    password = page.locator(
        'input[type="password"]:visible'
    ).first

    username.fill(cluster["login"])
    password.fill(cluster["password"])

    page.wait_for_timeout(500)
    password.press("Enter")
    page.wait_for_timeout(10000)

    body = page.locator("body").inner_text()

    if "Mot de passe oublié" in body or "Forgot your password" in body:
        raise Exception("Connexion refusée")


def extract_detail_url_from_html(row_html, base_url):
    decoded = html.unescape(row_html)

    patterns = [
        r"(/do/Network/iotGateway:[^'\"<>\s]+)",
        r"RQ\.nav\.detail\('([^']*iotGateway:[^']+)'",
        r"RQ\.nav\.go\('([^']*iotGateway:[^']+)'",
        r'href="([^"]*iotGateway:[^"]+)"',
        r"href='([^']*iotGateway:[^']+)'",
    ]

    for pattern in patterns:
        match = re.search(pattern, decoded, re.I)
        if match:
            return make_absolute_url(base_url, match.group(1))

    return ""


def parse_gateway(values, raw, cluster_name, detail_url=""):
    values = [clean(v) for v in values if clean(v)]

    gateway_id = ""

    for v in values:
        if re.fullmatch(r"[0-9A-Fa-f]{12,32}", v):
            gateway_id = v
            break

    if not gateway_id:
        return None

    status = ""

    for v in values:
        if v.lower() == "active":
            status = "Active"
            break

    if status != "Active":
        return None

    connection_raw = ""

    for v in values:
        low = v.lower()
        if (
            "connect" in low
            or "closed" in low
            or "offline" in low
            or "déconnect" in low
            or "deconnect" in low
        ):
            connection_raw = v
            break

    connection, is_down = normalize_connection(connection_raw)

    firmware = ""

    for v in values:
        if "mtcdt" in v.lower():
            firmware = v
            break

    model = ""

    for v in values:
        if "multitech" in v.lower() or "kerlink" in v.lower():
            model = v
            break

    name = gateway_id

    if "Active" in values:
        idx = values.index("Active")
        if idx > 0:
            name = values[idx - 1]

    geolocation = geoloc_from_text(raw)

    if not geolocation:
        for v in values:
            geolocation = geoloc_from_text(v)
            if geolocation:
                break

    city = ""

    if firmware and firmware in values:
        idx = values.index(firmware)
        if idx + 1 < len(values):
            city = values[idx + 1]

    if not city:
        for v in reversed(values):
            if (
                v
                and v not in [gateway_id, name, status, model, firmware, connection_raw, geolocation]
                and not geoloc_from_text(v)
                and len(v) < 80
            ):
                city = v
                break

    return {
        "cluster": cluster_name,
        "name": name,
        "status": "Active",
        "gateway_id": gateway_id,
        "model": model,
        "connection": connection,
        "firmware": firmware,
        "city": city,
        "geolocation": geolocation,
        "down": is_down,
        "detail_url": detail_url,
        "last_connection": None,
    }


def parse_ajax_html(html_text, cluster_name, base_url):
    found = {}

    rows = re.findall(
        r"<tr[^>]*>.*?</tr>",
        html_text,
        flags=re.I | re.S
    )

    for row_html in rows:
        raw = strip_tags(row_html)

        if not re.search(r"[0-9A-Fa-f]{12,32}", raw):
            continue

        cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            row_html,
            flags=re.I | re.S
        )

        values = [strip_tags(c) for c in cells]
        detail_url = extract_detail_url_from_html(row_html, base_url)

        gateway = parse_gateway(
            values,
            raw,
            cluster_name,
            detail_url
        )

        if gateway:
            found[gateway["gateway_id"]] = gateway

    return found


def collect_visible_rows(page, cluster):
    found = {}

    rows = page.locator("tr")

    for i in range(rows.count()):
        row = rows.nth(i)
        raw = clean(row.inner_text())

        if not re.search(r"[0-9A-Fa-f]{12,32}", raw):
            continue

        cells = row.locator("td")

        values = [
            cells.nth(j).inner_text()
            for j in range(cells.count())
        ]

        detail_url = ""

        try:
            href = row.locator("a").first.get_attribute("href")
            detail_url = make_absolute_url(cluster["url"], href)
        except Exception:
            pass

        try:
            if not detail_url:
                onclick = row.get_attribute("onclick") or ""
                match = re.search(r"RQ\.nav\.(?:detail|go)\('([^']+)'", onclick)
                if match:
                    detail_url = make_absolute_url(cluster["url"], match.group(1))
        except Exception:
            pass

        gateway = parse_gateway(
            values,
            raw,
            cluster["name"],
            detail_url
        )

        if gateway:
            found[gateway["gateway_id"]] = gateway

    return found


def click_next(page):
    clicked = page.evaluate("""
        () => {
            const els = Array.from(document.querySelectorAll("a,button,span,div"));
            const visible = el => {
                const r = el.getBoundingClientRect();
                const s = window.getComputedStyle(el);
                return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
            };

            for (const el of els) {
                if (!visible(el)) continue;

                const txt = (el.innerText || el.textContent || "").trim().toLowerCase();
                const cls = (el.className || "").toString().toLowerCase();
                const title = (el.getAttribute("title") || "").toLowerCase();
               
