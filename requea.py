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
    return " ".join(
        str(v or "")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace("\xa0", " ")
        .split()
    ).strip()


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
    ):
        return "Déconnectée", True

    if (
        "connectée" in t
        or "connectee" in t
        or "connected" in t
    ):
        return "Connectée", False

    return clean(v) or "Inconnue", True


def parse_last_connection(text):

    text = clean(text)

    patterns = [
        r"Dernière connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Derniere connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Last connection\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
    ]

    for pattern in patterns:

        m = re.search(pattern, text, re.IGNORECASE)

        if not m:
            continue

        try:
            dt = datetime.strptime(
                m.group(1),
                "%d/%m/%Y %H:%M:%S"
            )

            return dt.replace(tzinfo=PARIS)

        except Exception:
            pass

    return None


def geoloc_from_text(text):

    m = re.search(
        r"([0-9]{2}\.[0-9]+)[,\s]+([0-9]{1,2}\.[0-9]+)",
        text
    )

    if not m:
        return ""

    return f"{m.group(1)}, {m.group(2)}"


def login(page, cluster):

    page.goto(
        cluster["url"],
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(3000)

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


def parse_gateway(values, raw, cluster_name, detail_url=""):

    values = [clean(v) for v in values if clean(v)]

    if len(values) < 5:
        return None

    gateway_id = ""

    for v in values:

        if re.fullmatch(r"[0-9A-Fa-f]{12,32}", v):
            gateway_id = v
            break

    if not gateway_id:
        return None

    status = ""

    for v in values:

        if v.lower() in [
            "active",
            "inactive",
            "disabled",
            "deposée",
            "déposée",
        ]:
            status = v
            break

    # IMPORTANT :
    # on garde UNIQUEMENT Active
    # sinon Val Vanoise passe à 14

    if status.lower() != "active":
        return None

    connection_raw = ""

    for v in values:

        low = v.lower()

        if (
            "connect" in low
            or "closed" in low
            or "offline" in low
            or "déconnect" in low
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

    geolocation = geoloc_from_text(raw)

    name = ""

    if "Active" in values:

        idx = values.index("Active")

        if idx > 0:
            name = values[idx - 1]

    if not name:
        name = gateway_id

    city = ""

    if firmware and firmware in values:

        idx = values.index(firmware)

        if len(values) > idx + 1:
            city = values[idx + 1]

    return {
        "cluster": cluster_name,
        "name": name,
        "status": status,
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


def collect_rows(page, cluster):

    found = {}

    rows = page.locator("tr")
    count = rows.count()

    for i in range(count):

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

            if href:

                if href.startswith("/"):
                    detail_url = cluster["url"].rstrip("/") + href

                elif href.startswith("http"):
                    detail_url = href

                else:
                    detail_url = cluster["url"].rstrip("/") + "/" + href

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


def click_next_page(page):

    clicked = page.evaluate("""
        () => {

            const all = Array.from(
                document.querySelectorAll("a,button,span,div")
            )

            for (const el of all) {

                const txt =
                    (el.innerText || "").trim().toLowerCase()

                const cls =
                    (el.className || "").toString().toLowerCase()

                if (
                    txt === "suivant"
                    || txt === ">"
                    || txt === "›"
                    || cls.includes("next")
                ) {

                    if (cls.includes("disabled")) {
                        continue
                    }

                    el.click()
                    return true
                }
            }

            return false
        }
    """)

    if clicked:
        page.wait_for_timeout(6000)

    return clicked


def read_last_connection(page, gateway):

    if not gateway["detail_url"]:
        return None

    try:

        page.goto(
            gateway["detail_url"],
            wait_until="domcontentloaded",
            timeout=60000
        )

        page.wait_for_timeout(5000)

        body = page.locator("body").inner_text()

        last = parse_last_connection(body)

        return last

    except Exception:
        return None


def apply_history(gateway):

    key = gateway["gateway_id"]

    if key not in history:

        history[key] = {
            "down_since": None,
            "samples": []
        }

    # IMPORTANT :
    # on prend EXCLUSIVEMENT
    # la vraie dernière connexion

    if gateway["down"]:

        if gateway["last_connection"]:

            history[key]["down_since"] = (
                gateway["last_connection"]
            )

    else:

        history[key]["down_since"] = None

    history[key]["samples"].append({
        "time": NOW.isoformat(),
        "up": not gateway["down"]
    })

    history[key]["samples"] = [
        s for s in history[key]["samples"]
        if (
            NOW -
            datetime.fromisoformat(s["time"])
        ).total_seconds() <= 86400
    ]

    samples = history[key]["samples"]

    service_24h = round(
        (
            sum(1 for s in samples if s["up"])
            / len(samples)
        ) * 100,
        1
    ) if samples else 0

    down_hours = 0

    if history[key]["down_since"]:

        start = datetime.fromisoformat(
            history[key]["down_since"]
        )

        down_hours = round(
            (NOW - start).total_seconds() / 3600,
            1
        )

    gateway["down_since"] = (
        history[key]["down_since"]
    )

    gateway["down_hours"] = down_hours
    gateway["service_24h"] = service_24h
    gateway["maintenance"] = down_hours >= 24

    return gateway


with sync_playwright() as p:

    browser = p.chromium.launch(headless=True)

    for cluster in CONFIG:

        context = browser.new_context()
        page = context.new_page()

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

                current = collect_rows(
                    page,
                    cluster
                )

                for k, v in current.items():
                    seen[k] = v

                sig = "|".join(sorted(seen.keys()))

                if sig in visited:
                    break

                visited.add(sig)

                moved = click_next_page(page)

                if not moved:
                    break

            for gateway_id, gateway in seen.items():

                if gateway["down"]:

                    detail_page = context.new_page()

                    last = read_last_connection(
                        detail_page,
                        gateway
                    )

                    detail_page.close()

                    if last:
                        gateway["last_connection"] = (
                            last.isoformat()
                        )

                gateways.append(
                    apply_history(gateway)
                )

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
                "down_since": None,
                "down_hours": 0,
                "service_24h": 0,
                "maintenance": False
            })

        context.close()

    browser.close()


with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(
        history,
        f,
        indent=2,
        ensure_ascii=False
    )


active_gateways = gateways

total = len(active_gateways)

down = len([
    g for g in active_gateways
    if g["down"]
])

ok = total - down

maintenance = len([
    g for g in active_gateways
    if g["maintenance"]
])

service = round(
    ok / total * 100,
    1
) if total else 0

clusters = sorted(
    set(g["cluster"] for g in active_gateways)
)

cluster_stats = {}

for c in clusters:

    cg = [
        g for g in active_gateways
        if g["cluster"] == c
    ]

    c_total = len(cg)

    c_down = len([
        g for g in cg
        if g["down"]
    ])

    c_ok = c_total - c_down

    cluster_stats[c] = {
        "total": c_total,
        "ok": c_ok,
        "down": c_down,
        "service": round(
            c_ok / c_total * 100,
            1
        ) if c_total else 0
    }


html_page = f"""
<!DOCTYPE html>
<html>

<head>

<meta charset="utf-8">

<meta
    name="viewport"
    content="width=device-width, initial-scale=1"
>

<title>Monitoring Requea LoRaWAN</title>

<style>

body {{
    background:#0f172a;
    color:white;
    font-family:Arial;
    margin:0;
    padding:16px;
}}

.cards {{
    display:grid;
    grid-template-columns:
        repeat(auto-fit,minmax(180px,1fr));
    gap:12px;
    margin:20px 0;
}}

.card {{
    background:#1e293b;
    padding:16px;
    border-radius:14px;
}}

.big {{
    font-size:32px;
    font-weight:bold;
}}

.green {{
    color:#22c55e;
}}

.red {{
    color:#ef4444;
}}

.orange {{
    color:#f59e0b;
}}

button {{
    padding:9px 13px;
    border:0;
    border-radius:8px;
    margin:4px;
}}

.table-wrap {{
    overflow-x:auto;
    margin-bottom:40px;
}}

table {{
    width:100%;
    min-width:1350px;
    border-collapse:collapse;
}}

th {{
    background:#334155;
    padding:10px;
    text-align:left;
    white-space:nowrap;
}}

td {{
    padding:10px;
    border-bottom:1px solid #334155;
    white-space:nowrap;
}}

tr.down {{
    background:#451a1a;
}}

tr.maintenance {{
    background:#7f1d1d;
}}

.badge {{
    padding:5px 10px;
    border-radius:20px;
}}

.ok {{
    background:#166534;
}}

.ko {{
    background:#991b1b;
}}

</style>

<script>

function filterCluster(cluster) {{

    document
        .querySelectorAll(".gateway-row")
        .forEach(row => {{

            row.style.display =
                cluster === "ALL"
                || row.dataset.cluster === cluster
                ? ""
                : "none"
        }})
}}

</script>

</head>

<body>

<h1>📡 Monitoring Requea LoRaWAN</h1>

<p>
Dernière mise à jour :
{NOW.strftime("%d/%m/%Y %H:%M")}
</p>

<div class="cards">

<div class="card">
Clusters
<div class="big">{len(clusters)}</div>
</div>

<div class="card">
Passerelles Active
<div class="big">{total}</div>
</div>

<div class="card">
Taux service
<div class="big green">{service}%</div>
</div>

<div class="card">
Connectées
<div class="big green">{ok}</div>
</div>

<div class="card">
Défaillantes
<div class="big red">{down}</div>
</div>

<div class="card">
Maintenance >24h
<div class="big orange">{maintenance}</div>
</div>

</div>

<h2>🌍 Synthèse clusters</h2>

<div class="cards">
"""

for c in clusters:

    s = cluster_stats[c]

    color = (
        "green"
        if s["down"] == 0
        else "red"
    )

    html_page += f"""
<div class="card">

<strong>{esc(c)}</strong>

<div>
Passerelles : {s["total"]}
</div>

<div>
Connectées : {s["ok"]}
</div>

<div>
Défaillantes : {s["down"]}
</div>

<div class="{color}">
Service : {s["service"]}%
</div>

</div>
"""

html_page += """
</div>

<h2>🌍 Filtre cluster</h2>

<button onclick="filterCluster('ALL')">
Tous
</button>
"""

for c in clusters:

    html_page += f"""
<button onclick="filterCluster('{esc(c)}')">
{esc(c)}
</button>
"""

html_page += """
<h2>🚨 Passerelles à traiter</h2>

<div class="table-wrap">

<table>

<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>Géolocalisation</th>
<th>Connexion</th>
<th>Dernière connexion</th>
<th>HS depuis</th>
<th>Durée HS</th>
<th>Service 24h</th>
<th>Maintenance</th>
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
    data-cluster="{esc(g["cluster"])}"
>

<td>{esc(g["cluster"])}</td>

<td>{esc(g["name"])}</td>

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

<td>
{"OUI" if g["maintenance"] else "Non"}
</td>

<td>{esc(g["firmware"])}</td>

</tr>
"""

html_page += """
</table>
</div>

<h2>📋 Toutes les passerelles</h2>

<div class="table-wrap">

<table>

<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>Géolocalisation</th>
<th>Statut</th>
<th>Connexion</th>
<th>Service 24h</th>
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

    html_page += f"""
<tr
    class="gateway-row {row_class}"
    data-cluster="{esc(g["cluster"])}"
>

<td>{esc(g["cluster"])}</td>

<td>{esc(g["name"])}</td>

<td>{esc(g["city"])}</td>

<td>{esc(g["geolocation"])}</td>

<td>{esc(g["status"])}</td>

<td>
<span class="badge {badge}">
{esc(g["connection"])}
</span>
</td>

<td>{g["service_24h"]}%</td>

<td>{esc(g["firmware"])}</td>

<td>{esc(g["gateway_id"])}</td>

</tr>
"""

html_page += """
</table>
</div>

</body>
</html>
"""

os.makedirs(
    "public",
    exist_ok=True
)

with open(
    "public/index.html",
    "w",
    encoding="utf-8"
) as f:

    f.write(html_page)

print(
    f"Dashboard généré : {total} passerelles actives"
)
