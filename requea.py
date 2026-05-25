from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import os
import html
import re

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)

HISTORY_FILE = "history.json"

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
except:
    history = {}

gateways = []
debug = []


def esc(v):
    return html.escape(str(v or ""))


def fmt(v):
    if not v:
        return "-"
    try:
        return datetime.fromisoformat(v).astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(v)


def clean(v):
    return " ".join(str(v).replace("\n", " ").split()).strip()


def connection_state(v):
    t = str(v).lower()

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

    return v, False


def extract_last_connection(text):

    m = re.search(
        r"Dernière connexion\s*:\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text
    )

    if not m:
        return None

    try:
        return datetime.strptime(
            m.group(1),
            "%d/%m/%Y %H:%M:%S"
        ).replace(tzinfo=PARIS)

    except:
        return None


with sync_playwright() as p:

    browser = p.chromium.launch(headless=True)

    for cluster in CONFIG:

        page = browser.new_page()

        try:

            page.goto(
                cluster["url"],
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(4000)

            # IMPORTANT :
            # on cible uniquement les champs visibles

            login_input = page.locator(
                'input[type="text"]:visible, input[name*="login"]:visible'
            ).first

            password_input = page.locator(
                'input[type="password"]:visible'
            ).first

            login_input.fill(cluster["login"])
            password_input.fill(cluster["password"])

            page.wait_for_timeout(1000)

            submit = page.locator(
                'button[type="submit"]:visible, input[type="submit"]:visible'
            ).first

            submit.click()

            page.wait_for_timeout(10000)

            debug.append(
                f"{cluster['name']} LOGIN URL={page.url}"
            )

            body = page.locator("body").inner_text()

            if "Mot de passe oublié" in body:
                raise Exception("ECHEC LOGIN")

            page.goto(
                f"{cluster['url']}/page/Network_Gateways",
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(12000)

            rows = page.locator("tr")

            count = rows.count()

            debug.append(
                f"{cluster['name']} ROWS={count}"
            )

            for i in range(count):

                row = rows.nth(i)

                raw = clean(row.inner_text())

                if "Active" not in raw:
                    continue

                cells = row.locator("td")

                values = []

                for j in range(cells.count()):

                    val = clean(
                        cells.nth(j).inner_text()
                    )

                    if val:
                        values.append(val)

                if len(values) < 5:
                    continue

                name = values[0]

                status = "Active"

                gateway_id = ""

                for v in values:
                    if re.fullmatch(r"[0-9A-Fa-f]{12,32}", v):
                        gateway_id = v
                        break

                firmware = ""

                for v in values:
                    if "mtcdt" in v.lower():
                        firmware = v
                        break

                connection_raw = ""

                for v in values:
                    low = v.lower()

                    if (
                        "connect" in low
                        or "closed" in low
                        or "offline" in low
                    ):
                        connection_raw = v
                        break

                connection, is_down = connection_state(
                    connection_raw
                )

                geoloc = ""

                geo_match = re.search(
                    r"([0-9]{2}\.[0-9]+)[,\s]+([0-9]{1,2}\.[0-9]+)",
                    raw
                )

                if geo_match:
                    geoloc = (
                        geo_match.group(1)
                        + ", "
                        + geo_match.group(2)
                    )

                city = ""

                if firmware in values:

                    idx = values.index(firmware)

                    if len(values) > idx + 1:
                        city = values[idx + 1]

                last_connection = None

                if is_down:

                    try:

                        link = row.locator("a").first

                        link.click()

                        page.wait_for_timeout(4000)

                        detail_text = page.locator(
                            "body"
                        ).inner_text()

                        last_dt = extract_last_connection(
                            detail_text
                        )

                        if last_dt:
                            last_connection = last_dt.isoformat()

                        page.go_back()

                        page.wait_for_timeout(8000)

                        rows = page.locator("tr")

                    except Exception as e:

                        debug.append(
                            f"DETAIL ERROR {name} {e}"
                        )

                if not gateway_id:
                    gateway_id = (
                        cluster["name"] + "-" + name
                    )

                if gateway_id not in history:

                    history[gateway_id] = {
                        "down_since": None,
                        "samples": []
                    }

                if is_down:

                    if last_connection:
                        history[gateway_id][
                            "down_since"
                        ] = last_connection

                    elif not history[gateway_id][
                        "down_since"
                    ]:
                        history[gateway_id][
                            "down_since"
                        ] = NOW.isoformat()

                else:

                    history[gateway_id][
                        "down_since"
                    ] = None

                history[gateway_id]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not is_down
                })

                history[gateway_id]["samples"] = [

                    s for s in
                    history[gateway_id]["samples"]

                    if (
                        NOW -
                        datetime.fromisoformat(s["time"])
                    ).total_seconds() <= 86400
                ]

                service = 0

                samples = history[gateway_id]["samples"]

                if samples:

                    up = sum(
                        1 for s in samples if s["up"]
                    )

                    service = round(
                        up / len(samples) * 100,
                        1
                    )

                down_hours = 0

                if history[gateway_id]["down_since"]:

                    start = datetime.fromisoformat(
                        history[gateway_id]["down_since"]
                    )

                    down_hours = round(
                        (
                            NOW - start
                        ).total_seconds() / 3600,
                        1
                    )

                gateways.append({

                    "cluster": cluster["name"],
                    "name": name,
                    "city": city,
                    "geolocation": geoloc,
                    "status": status,
                    "connection": connection,
                    "down": is_down,
                    "gateway_id": gateway_id,
                    "firmware": firmware,
                    "last_connection": last_connection,
                    "down_since": history[gateway_id]["down_since"],
                    "down_hours": down_hours,
                    "service_24h": service,
                    "maintenance": down_hours >= 24
                })

        except Exception as e:

            debug.append(
                f"ERREUR {cluster['name']} : {e}"
            )

        page.close()

    browser.close()

with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(
        history,
        f,
        indent=2,
        ensure_ascii=False
    )

total = len(gateways)

down = sum(
    1 for g in gateways if g["down"]
)

ok = total - down

maintenance = sum(
    1 for g in gateways if g["maintenance"]
)

service = round(
    ok / total * 100,
    1
) if total else 0

clusters = sorted(
    set(g["cluster"] for g in gateways)
)

html_page = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

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

table {{
    width:100%;
    min-width:1400px;
    border-collapse:collapse;
}}

.table-wrap {{
    overflow-x:auto;
}}

th {{
    background:#334155;
    padding:10px;
    text-align:left;
}}

td {{
    padding:10px;
    border-bottom:1px solid #334155;
}}

.down {{
    background:#451a1a;
}}

.maintenance {{
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

button {{
    padding:8px 12px;
    border:0;
    border-radius:8px;
    margin:4px;
}}

pre {{
    background:#111827;
    padding:12px;
    overflow:auto;
}}

</style>

<script>

function filterCluster(cluster) {{

    document
    .querySelectorAll(".gateway")
    .forEach(row => {{

        if (
            cluster === "ALL"
            || row.dataset.cluster === cluster
        ) {{
            row.style.display = "";
        }}
        else {{
            row.style.display = "none";
        }}

    }});

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
<div class="big">
{len(clusters)}
</div>
</div>

<div class="card">
Passerelles Active
<div class="big">
{total}
</div>
</div>

<div class="card">
Taux service instantané
<div class="big green">
{service}%
</div>
</div>

<div class="card">
Connectées
<div class="big green">
{ok}
</div>
</div>

<div class="card">
Défaillantes
<div class="big red">
{down}
</div>
</div>

<div class="card">
Maintenance >24h
<div class="big orange">
{maintenance}
</div>
</div>

</div>

<h2>🌍 Clusters</h2>

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

for g in gateways:

    if not g["down"]:
        continue

    cls = (
        "maintenance"
        if g["maintenance"]
        else "down"
    )

    html_page += f"""
<tr
class="gateway {cls}"
data-cluster="{esc(g['cluster'])}"
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

<td>{fmt(g["last_connection"])}</td>
<td>{fmt(g["down_since"])}</td>
<td>{g["down_hours"]} h</td>
<td>{g["service_24h"]}%</td>
<td>{"OUI" if g["maintenance"] else "Non"}</td>
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

for g in gateways:

    cls = (
        "maintenance"
        if g["maintenance"]
        else (
            "down"
            if g["down"]
            else ""
        )
    )

    badge = (
        "ko"
        if g["down"]
        else "ok"
    )

    html_page += f"""
<tr
class="gateway {cls}"
data-cluster="{esc(g['cluster'])}"
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

<h2>Debug lecture Requea</h2>
<pre>
"""

for d in debug:
    html_page += esc(d) + "\n"

html_page += """
</pre>

</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open(
    "public/index.html",
    "w",
    encoding="utf-8"
) as f:
    f.write(html_page)

print(
    f"Dashboard généré : {total} passerelles"
)
