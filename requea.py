from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import os, json, html, re

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
    return " ".join(str(v or "").replace("\n", " ").replace("\t", " ").replace("\xa0", " ").split()).strip()


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


def parse_requea_date(text):
    text = html.unescape(str(text or ""))
    text = clean(text)

    m = re.search(
        r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if not m:
        return None

    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
    except Exception:
        return None


def parse_last_connection_from_html(html_text):
    decoded = html.unescape(str(html_text or ""))

    patterns = [
        r"Derni[eè]re\s+connexion[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Derniere\s+connexion[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Last\s+connection[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
    ]

    for pattern in patterns:
        m = re.search(pattern, decoded, re.I | re.S)
        if m:
            return parse_requea_date(m.group(1))

    text = strip_tags(decoded)

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            return parse_requea_date(m.group(1))

    return None


def normalize_connection(v):
    t = str(v or "").lower()

    if "déconnect" in t or "deconnect" in t or "closed" in t or "offline" in t or "down" in t:
        return "Déconnectée", True

    if "connectée" in t or "connectee" in t or "connected" in t or "online" in t:
        return "Connectée", False

    return clean(v) or "Inconnue", True


def geoloc_from_text(text):
    text = clean(text)

    patterns = [
        r"([0-9]{2}\.[0-9]+)\s*,\s*([0-9]{1,2}\.[0-9]+)",
        r"([0-9]{2}\.[0-9]+)\s+([0-9]{1,2}\.[0-9]+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return f"{m.group(1)}, {m.group(2)}"

    return ""


def make_absolute_url(base_url, url):
    if not url:
        return ""

    url = html.unescape(url).replace("&amp;", "&")

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
        r"(/do/iotGateway:get\?sysId=[^'\"&<>\s]+[^'\"<>\s]*)",
        r"RQ\.nav\.detail\('([^']*iotGateway:get[^']*)'",
        r"RQ\.nav\.go\('([^']*iotGateway:get[^']*)'",
        r'href="([^"]*iotGateway:get[^"]*)"',
        r"href='([^']*iotGateway:get[^']*)'",
    ]

    for pattern in patterns:
        m = re.search(pattern, decoded, re.I)
        if m:
            return make_absolute_url(base_url, m.group(1))

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
        if "connect" in low or "closed" in low or "offline" in low or "déconnect" in low or "deconnect" in low:
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
        "connected_since": None,
    }


def parse_ajax_html(html_text, cluster_name, base_url):
    found = {}

    rows = re.findall(r"<tr[^>]*>.*?</tr>", html_text, flags=re.I | re.S)

    for row_html in rows:
        raw = strip_tags(row_html)

        if not re.search(r"[0-9A-Fa-f]{12,32}", raw):
            continue

        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
        values = [strip_tags(c) for c in cells]

        detail_url = extract_detail_url_from_html(row_html, base_url)

        gateway = parse_gateway(values, raw, cluster_name, detail_url)

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
        values = [cells.nth(j).inner_text() for j in range(cells.count())]

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

        gateway = parse_gateway(values, raw, cluster["name"], detail_url)

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
        const aria = (el.getAttribute("aria-label") || "").toLowerCase();

        if (cls.includes("disabled")) continue;
        if (el.getAttribute("disabled") !== null) continue;

        if (
            txt === ">" ||
            txt === "›" ||
            txt === "suivant" ||
            txt === "next" ||
            cls.includes("next") ||
            title.includes("suivant") ||
            title.includes("next") ||
            aria.includes("suivant") ||
            aria.includes("next")
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


def read_connection_date(context, cluster, gateway):
    detail_url = gateway.get("detail_url") or ""

    p = context.new_page()

    try:
        if detail_url:
            p.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
            p.wait_for_timeout(6000)

            body_text = p.locator("body").inner_text()
            html_detail = p.content()

            last = parse_last_connection_from_html(body_text)

            if not last:
                last = parse_last_connection_from_html(html_detail)

            gps = geoloc_from_text(body_text)

            if gps:
                gateway["geolocation"] = gps

            if last:
                print("DATE TROUVEE", gateway["name"], last.strftime("%d/%m/%Y %H:%M:%S"))

            p.close()
            return last

    except Exception:
        pass

    try:
        p.close()
    except Exception:
        pass

    return None


def apply_history(g):
    key = g["gateway_id"]

    if key not in history:
        history[key] = {
            "down_since": None,
            "samples": []
        }

    if g["down"]:
        if g["last_connection"]:
            history[key]["down_since"] = g["last_connection"]
        else:
            history[key]["down_since"] = None
    else:
        history[key]["down_since"] = None

    history[key]["samples"].append({
        "time": NOW.isoformat(),
        "up": not g["down"]
    })

    history[key]["samples"] = [
        s for s in history[key]["samples"]
        if (NOW - datetime.fromisoformat(s["time"])).total_seconds() <= 86400
    ]

    samples = history[key]["samples"]

    g["service_24h"] = (
        round(sum(1 for s in samples if s["up"]) / len(samples) * 100, 1)
        if samples else 0
    )

    g["down_since"] = history[key]["down_since"]
    g["down_hours"] = 0

    if g["down_since"]:
        start = datetime.fromisoformat(g["down_since"])
        g["down_hours"] = round((NOW - start).total_seconds() / 3600, 1)

    g["maintenance"] = g["down_hours"] >= 24

    if not g["down"]:
        g["connected_since"] = g["last_connection"]

    return g


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    for cluster in CONFIG:
        context = browser.new_context()
        page = context.new_page()

        ajax_payloads = []

        def on_response(response):
            try:
                if "/ajax" in response.url:
                    txt = response.text()
                    if (
                        "iotGateway" in txt
                        or "mtcdt" in txt
                        or re.search(r"[0-9A-Fa-f]{12,32}", txt)
                    ):
                        ajax_payloads.append(txt)
            except Exception:
                pass

        page.on("response", on_response)

        try:
            login(page, cluster)

            page.goto(
                f'{cluster["url"]}/page/Network_Gateways',
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(12000)

            seen = {}
            visited = set()

            for _ in range(20):
                for k, v in collect_visible_rows(page, cluster).items():
                    seen[k] = v

                for payload in ajax_payloads:
                    for k, v in parse_ajax_html(payload, cluster["name"], cluster["url"]).items():
                        seen[k] = v

                sig = "|".join(sorted(seen.keys()))

                if sig in visited:
                    break

                visited.add(sig)

                if not click_next(page):
                    break

            for gateway_id, gateway in seen.items():
                connection_date = read_connection_date(context, cluster, gateway)

                if connection_date:
                    gateway["last_connection"] = connection_date.isoformat()

                if not gateway["down"]:
                    gateway["connected_since"] = gateway["last_connection"]

                gateways.append(apply_history(gateway))

        except Exception as e:
            gateways.append({
                "cluster": cluster["name"],
                "name": "ERREUR",
                "status": "Erreur",
                "gateway_id": "",
                "model": "",
                "connection": str(e),
                "firmware": "",
                "city": "",
                "geolocation": "",
                "down": True,
                "detail_url": "",
                "last_connection": None,
                "connected_since": None,
                "down_since": None,
                "down_hours": 0,
                "service_24h": 0,
                "maintenance": False
            })

        context.close()

    browser.close()


with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)


active_gateways = [g for g in gateways if g["status"] == "Active"]

total = len(active_gateways)
down = len([g for g in active_gateways if g["down"]])
ok = total - down
maintenance = len([g for g in active_gateways if g["maintenance"]])
service = round(ok / total * 100, 1) if total else 0

clusters = sorted(set(g["cluster"] for g in active_gateways))

cluster_stats = {}

for c in clusters:
    cg = [g for g in active_gateways if g["cluster"] == c]

    c_total = len(cg)
    c_down = len([g for g in cg if g["down"]])
    c_ok = c_total - c_down

    cluster_stats[c] = {
        "total": c_total,
        "ok": c_ok,
        "down": c_down,
        "service": round(c_ok / c_total * 100, 1) if c_total else 0
    }


html_page = f"""
<!DOCTYPE html>
<html lang="fr">

<head>

<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Monitoring Requea</title>

<style>

:root {{

    --bg:#f3f7fd;

    --glass:
        rgba(255,255,255,.38);

    --glass-strong:
        rgba(255,255,255,.56);

    --line:
        rgba(255,255,255,.58);

    --text:#0f172a;
    --muted:#64748b;

    --shadow:
        0 10px 40px rgba(15,23,42,.08),
        0 2px 12px rgba(15,23,42,.05);

    --blue:#4f8cff;
    --green:#16c784;
    --red:#ff5d73;
    --orange:#ffae4b;
    --purple:#8b6fff;
    --cyan:#53c8ff;

}}

* {{
    box-sizing:border-box;
}}

body {{

    margin:0;
    padding:18px;

    font-family:
        -apple-system,
        BlinkMacSystemFont,
        "SF Pro Display",
        sans-serif;

    color:var(--text);

    background:

        radial-gradient(
            circle at top left,
            rgba(79,140,255,.16),
            transparent 30%
        ),

        radial-gradient(
            circle at top right,
            rgba(139,111,255,.18),
            transparent 30%
        ),

        radial-gradient(
            circle at bottom center,
            rgba(83,200,255,.16),
            transparent 34%
        ),

        linear-gradient(
            180deg,
            #ffffff,
            #eef4ff
        );

}}

.container {{
    max-width:1700px;
    margin:auto;
}}

.glass {{

    background:var(--glass);

    backdrop-filter:
        blur(32px)
        saturate(180%);

    -webkit-backdrop-filter:
        blur(32px)
        saturate(180%);

    border:1px solid var(--line);

    box-shadow:var(--shadow);

}}

.hero {{

    position:relative;

    overflow:hidden;

    border-radius:38px;

    padding:34px;

    margin-bottom:24px;

}}

.hero::before {{

    content:"";

    position:absolute;
    inset:0;

    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,.30),
            rgba(255,255,255,.06)
        );

}}

.hero > * {{
    position:relative;
    z-index:2;
}}

.header {{

    display:flex;

    align-items:center;
    justify-content:space-between;

    gap:18px;

    flex-wrap:wrap;

}}

.brand {{

    display:flex;
    align-items:center;
    gap:18px;

}}

.logo {{

    width:78px;
    height:78px;

    border-radius:28px;

    display:flex;
    align-items:center;
    justify-content:center;

    font-size:34px;

    color:white;

    background:
        linear-gradient(
            145deg,
            #4f8cff,
            #8b6fff
        );

    box-shadow:
        0 20px 40px rgba(79,140,255,.28);

}}

.title-wrap h1 {{

    margin:0;

    font-size:44px;

    font-weight:900;

    letter-spacing:-2px;

}}

.subtitle {{

    margin-top:8px;

    color:var(--muted);

    font-size:15px;

}}

.updated {{

    padding:14px 18px;

    border-radius:999px;

    background:
        rgba(255,255,255,.45);

    border:
        1px solid rgba(255,255,255,.65);

    font-weight:700;

    backdrop-filter:blur(18px);

}}

.cards {{

    margin-top:30px;

    display:grid;

    grid-template-columns:
        repeat(auto-fit,minmax(220px,1fr));

    gap:18px;

}}

.card {{

    position:relative;

    overflow:hidden;

    border-radius:30px;

    padding:24px;

    min-height:175px;

    color:white;

    box-shadow:
        0 20px 45px rgba(15,23,42,.10);

}}

.card::before {{

    content:"";

    position:absolute;
    inset:0;

    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,.30),
            rgba(255,255,255,.05)
        );

}}

.card::after {{

    content:"";

    position:absolute;

    width:140px;
    height:140px;

    right:-40px;
    top:-40px;

    border-radius:999px;

    background:
        rgba(255,255,255,.18);

}}

.card > * {{
    position:relative;
    z-index:2;
}}

.card-icon {{

    width:56px;
    height:56px;

    border-radius:18px;

    display:flex;
    align-items:center;
    justify-content:center;

    background:
        rgba(255,255,255,.20);

    backdrop-filter:blur(18px);

    font-size:25px;

}}

.card-label {{

    margin-top:18px;

    font-size:15px;

    font-weight:700;

}}

.card-big {{

    margin-top:10px;

    font-size:44px;

    font-weight:900;

    letter-spacing:-2px;

}}

.blue {{
    background:
        linear-gradient(145deg,#4f8cff,#67b4ff);
}}

.green {{
    background:
        linear-gradient(145deg,#16c784,#4ade80);
}}

.red {{
    background:
        linear-gradient(145deg,#ff5d73,#ff8aa0);
}}

.orange {{
    background:
        linear-gradient(145deg,#ffae4b,#ffd36a);
}}

.purple {{
    background:
        linear-gradient(145deg,#8b6fff,#b39cff);
}}

.cyan {{
    background:
        linear-gradient(145deg,#3dc7ff,#73e4ff);
}}

.panel {{

    border-radius:34px;

    padding:28px;

    margin-bottom:24px;

}}

.panel-title {{

    font-size:30px;

    font-weight:900;

    letter-spacing:-1px;

    margin-bottom:22px;

}}

.cluster-grid {{

    display:grid;

    grid-template-columns:
        repeat(auto-fit,minmax(260px,1fr));

    gap:18px;

}}

.cluster-card {{

    position:relative;

    overflow:hidden;

    border-radius:30px;

    padding:24px;

    background:
        rgba(255,255,255,.40);

    border:
        1px solid rgba(255,255,255,.68);

    backdrop-filter:
        blur(26px);

}}

.cluster-card::before {{

    content:"";

    position:absolute;
    inset:0;

    background:
        linear-gradient(
            145deg,
            rgba(255,255,255,.26),
            rgba(255,255,255,.05)
        );

}}

.cluster-card > * {{
    position:relative;
    z-index:2;
}}

.cluster-top {{

    display:flex;
    align-items:center;
    gap:14px;

}}

.cluster-logo {{

    width:58px;
    height:58px;

    border-radius:20px;

    display:flex;
    align-items:center;
    justify-content:center;

    font-size:24px;

    color:white;

    background:
        linear-gradient(
            145deg,
            #4f8cff,
            #8b6fff
        );

}}

.cluster-name {{

    font-size:22px;

    font-weight:900;

    letter-spacing:-1px;

}}

.cluster-stats {{

    margin-top:20px;

    display:grid;

    grid-template-columns:1fr 1fr 1fr;

    gap:14px;

}}

.cluster-number {{

    font-size:28px;

    font-weight:900;

}}

.cluster-sub {{

    color:var(--muted);

    font-size:13px;

}}

.progress-wrap {{
    margin-top:22px;
}}

.progress-bar {{

    height:12px;

    border-radius:999px;

    overflow:hidden;

    background:
        rgba(226,232,240,.82);

}}

.progress-fill {{

    height:100%;

    border-radius:999px;

    background:
        linear-gradient(
            90deg,
            #4f8cff,
            #53c8ff,
            #4ade80
        );

    box-shadow:
        0 0 18px rgba(79,140,255,.42);

}}

.progress-label {{

    margin-top:8px;

    text-align:right;

    font-weight:800;

}}

.segmented-wrap {{
    overflow-x:auto;
}}

.segmented {{

    position:relative;

    display:inline-flex;

    gap:6px;

    padding:8px;

    border-radius:999px;

    background:
        rgba(255,255,255,.46);

    border:
        1px solid rgba(255,255,255,.72);

    min-width:max-content;

}}

.slider {{

    position:absolute;

    top:8px;
    left:8px;

    height:calc(100% - 16px);

    border-radius:999px;

    background:
        linear-gradient(
            145deg,
            #4f8cff,
            #8b6fff
        );

    transition:
        transform .28s cubic-bezier(.2,.8,.2,1),
        width .28s cubic-bezier(.2,.8,.2,1);

    box-shadow:
        0 10px 22px rgba(79,140,255,.30);

}}

.seg-btn {{

    position:relative;

    z-index:2;

    border:0;

    background:transparent;

    padding:13px 22px;

    border-radius:999px;

    font-weight:800;

    color:#334155;

    cursor:pointer;

    white-space:nowrap;

}}

.seg-btn.active {{
    color:white;
}}

.table-wrap {{

    overflow:auto;

    border-radius:28px;

    border:
        1px solid rgba(255,255,255,.68);

}}

table {{
    width:100%;
    min-width:1450px;
    border-collapse:collapse;
}}

th {{

    position:sticky;
    top:0;

    z-index:3;

    background:
        rgba(255,255,255,.72);

    backdrop-filter:blur(20px);

    color:#475569;

    padding:16px;

    text-align:left;

    font-size:13px;

}}

td {{

    padding:16px;

    border-bottom:
        1px solid rgba(148,163,184,.14);

    white-space:nowrap;

}}

tr:hover {{
    background:
        rgba(255,255,255,.32);
}}

.badge {{

    display:inline-flex;

    align-items:center;

    padding:8px 12px;

    border-radius:999px;

    font-size:12px;

    font-weight:800;

}}

.ok {{
    background:#dcfce7;
    color:#166534;
}}

.ko {{
    background:#fee2e2;
    color:#991b1b;
}}

.down {{
    background:
        rgba(254,226,226,.24);
}}

.maintenance {{
    background:
        rgba(255,237,213,.32);
}}

@media(max-width:1100px) {{

    .cards {{
        grid-template-columns:
            repeat(2,1fr);
    }}

}}

@media(max-width:760px) {{

    body {{
        padding:10px;
    }}

    .hero,
    .panel {{
        padding:18px;
        border-radius:24px;
    }}

    .title-wrap h1 {{
        font-size:30px;
    }}

    .cards {{
        grid-template-columns:1fr;
    }}

    .cluster-grid {{
        grid-template-columns:1fr;
    }}

}}

</style>

<script>

function initSegmented() {{

    const buttons =
        document.querySelectorAll(".seg-btn");

    const slider =
        document.querySelector(".slider");

    function move(btn) {{

        slider.style.width =
            btn.offsetWidth + "px";

        slider.style.transform =
            `translateX(${{btn.offsetLeft}}px)`;

    }}

    buttons.forEach(btn => {{

        btn.addEventListener("click", () => {{

            buttons.forEach(
                b => b.classList.remove("active")
            );

            btn.classList.add("active");

            move(btn);

            const cluster =
                btn.dataset.cluster;

            document
                .querySelectorAll(".gateway-row")
                .forEach(row => {{

                row.style.display =

                    cluster === "ALL"
                    || row.dataset.cluster === cluster

                    ? ""

                    : "none";

            }});

        }});

    }});

    move(
        document.querySelector(
            ".seg-btn.active"
        )
    );

}}

window.addEventListener(
    "load",
    initSegmented
);

</script>

</head>

<body>

<div class="container">

<div class="hero glass">

<div class="header">

<div class="brand">

<div class="logo">
📡
</div>

<div class="title-wrap">

<h1>
Monitoring Requea
</h1>

<div class="subtitle">
Infrastructure LoRaWAN • Supervision temps réel
</div>

</div>

</div>

<div class="updated">
⏱ {NOW.strftime("%d/%m/%Y %H:%M")}
</div>

</div>

<div class="cards">

<div class="card blue">
<div class="card-icon">🌐</div>
<div class="card-label">Clusters</div>
<div class="card-big">{len(clusters)}</div>
</div>

<div class="card cyan">
<div class="card-icon">📡</div>
<div class="card-label">Passerelles</div>
<div class="card-big">{total}</div>
</div>

<div class="card green">
<div class="card-icon">✅</div>
<div class="card-label">Connectées</div>
<div class="card-big">{ok}</div>
</div>

<div class="card red">
<div class="card-icon">🚨</div>
<div class="card-label">Déconnectées</div>
<div class="card-big">{down}</div>
</div>

<div class="card orange">
<div class="card-icon">📈</div>
<div class="card-label">Service</div>
<div class="card-big">{service}%</div>
</div>

<div class="card purple">
<div class="card-icon">🛠</div>
<div class="card-label">Maintenance</div>
<div class="card-big">{maintenance}</div>
</div>

</div>

</div>

<div class="panel glass">

<div class="panel-title">
🌍 Synthèse clusters
</div>

<div class="cluster-grid">
"""

for c in clusters:

    s = cluster_stats[c]

    html_page += f"""
<div class="cluster-card">

<div class="cluster-top">

<div class="cluster-logo">
📶
</div>

<div class="cluster-name">
{esc(c)}
</div>

</div>

<div class="cluster-stats">

<div>
<div class="cluster-number">
{s["total"]}
</div>
<div class="cluster-sub">
Total
</div>
</div>

<div>
<div class="cluster-number">
{s["ok"]}
</div>
<div class="cluster-sub">
Connectées
</div>
</div>

<div>
<div class="cluster-number">
{s["down"]}
</div>
<div class="cluster-sub">
HS
</div>
</div>

</div>

<div class="progress-wrap">

<div class="progress-bar">
<div
class="progress-fill"
style="width:{s["service"]}%">
</div>
</div>

<div class="progress-label">
{s["service"]}%
</div>

</div>

</div>
"""

html_page += """
</div>

</div>

<div class="panel glass">

<div class="panel-title">
🌍 Filtre clusters
</div>

<div class="segmented-wrap">

<div class="segmented">

<div class="slider"></div>

<button
class="seg-btn active"
data-cluster="ALL">

Tous

</button>
"""

for c in clusters:

    html_page += f"""
<button
class="seg-btn"
data-cluster="{esc(c)}">

{esc(c)}

</button>
"""

html_page += """
</div>
</div>

</div>

<div class="panel glass">

<div class="panel-title">
🚨 Passerelles HS
</div>

<div class="table-wrap">

<table>

<tr>

<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>GPS</th>
<th>Connexion</th>
<th>Dernière connexion</th>
<th>HS depuis</th>
<th>Durée HS</th>
<th>Service 24h</th>
<th>Firmware</th>

</tr>
"""

for g in active_gateways:

    if not g["down"]:
        continue

    row_class = (
        "maintenance"
        if g["maintenance"]
        else "down"
    )

    html_page += f"""
<tr
class="gateway-row {row_class}"
data-cluster="{esc(g["cluster"])}">

<td>{esc(g["cluster"])}</td>

<td>
<strong>{esc(g["name"])}</strong>
</td>

<td>{esc(g["city"])}</td>

<td>{esc(g["geolocation"])}</td>

<td>
<span class="badge ko">
{esc(g["connection"])}
</span>
</td>

<td>{fmt_date(g["last_connection"])}</td>

<td>{fmt_date(g["down_since"])}</td>

<td>{g["down_hours"]} h</td>

<td>{g["service_24h"]}%</td>

<td>{esc(g["firmware"])}</td>

</tr>
"""

html_page += """
</table>
</div>
</div>

<div class="panel glass">

<div class="panel-title">
📋 Toutes les passerelles
</div>

<div class="table-wrap">

<table>

<tr>

<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>GPS</th>
<th>Statut</th>
<th>Connexion</th>
<th>Connecté depuis</th>
<th>Dernière connexion</th>
<th>Firmware</th>
<th>ID</th>

</tr>
"""

for g in active_gateways:

    badge = (
        "ko"
        if g["down"]
        else "ok"
    )

    row_class = (
        "maintenance"
        if g["maintenance"]
        else (
            "down"
            if g["down"]
            else ""
        )
    )

    connected_since = (
        g["connected_since"]
        if not g["down"]
        else None
    )

    html_page += f"""
<tr
class="gateway-row {row_class}"
data-cluster="{esc(g["cluster"])}">

<td>{esc(g["cluster"])}</td>

<td>
<strong>{esc(g["name"])}</strong>
</td>

<td>{esc(g["city"])}</td>

<td>{esc(g["geolocation"])}</td>

<td>
<span class="badge ok">
{esc(g["status"])}
</span>
</td>

<td>
<span class="badge {badge}">
{esc(g["connection"])}
</span>
</td>

<td>{fmt_date(connected_since)}</td>

<td>{fmt_date(g["last_connection"])}</td>

<td>{esc(g["firmware"])}</td>

<td>{esc(g["gateway_id"])}</td>

</tr>
"""

html_page += """

</table>

</div>

</div>

</div>

</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open("public/index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Dashboard généré : {total} passerelles actives")
