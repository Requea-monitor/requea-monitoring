from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import os
import json
import html

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)

HISTORY_FILE = "history.json"

# =========================
# HISTORY
# =========================

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
except:
    history = {}

gateways = []


# =========================
# HELPERS
# =========================

def esc(v):
    return html.escape(str(v or ""))


def fmt_date(v):

    if not v:
        return "-"

    try:
        dt = datetime.fromisoformat(v)
        return dt.astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except:
        return str(v)


def normalize_connection(value):

    v = str(value).lower()

    if (
        "déconnect" in v
        or "deconnect" in v
        or "closed" in v
        or "offline" in v
    ):
        return "Déconnectée", True

    if (
        "connectée" in v
        or "connectee" in v
        or "connected" in v
    ):
        return "Connectée", False

    return value, False


# =========================
# SCRAP
# =========================

with sync_playwright() as p:

    browser = p.chromium.launch(headless=True)

    for cluster in CONFIG:

        page = browser.new_page()

        try:

            # LOGIN
            page.goto(
                cluster["url"],
                wait_until="networkidle",
                timeout=60000
            )

            page.fill(
                'input[type="text"]',
                cluster["login"]
            )

            page.fill(
                'input[type="password"]',
                cluster["password"]
            )

            page.click('button[type="submit"]')

            page.wait_for_timeout(5000)

            # PAGE PASSERELLES
            page.goto(
                f'{cluster["url"]}/page/Network_Gateways',
                wait_until="networkidle",
                timeout=60000
            )

            page.wait_for_timeout(8000)

            rows = page.locator("tr")
            count = rows.count()

            for i in range(count):

                row = rows.nth(i)

                txt = row.inner_text().strip()

                if "Active" not in txt:
                    continue

                cells = row.locator("td")

                values = []

                for j in range(cells.count()):

                    value = cells.nth(j).inner_text()

                    value = " ".join(
                        value.replace("\n", " ").split()
                    ).strip()

                    values.append(value)

                if len(values) < 10:
                    continue

                # TABLE REQUEA
                #
                # 0 nom
                # 1 état
                # 2 identifiant
                # 3 modèle
                # 4 connexion
                # 5 réseau
                # 6 firmware
                # 7 commune
                # 8 géoloc

                name = values[0]
                status = values[1]
                gateway_id = values[2]
                model = values[3]
                raw_connection = values[4]
                network = values[5]
                firmware = values[6]
                city = values[7]
                geolocation = values[8]

                connection, is_down = normalize_connection(
                    raw_connection
                )

                last_connection = None

                # =========================
                # DETAIL UNIQUEMENT SI HS
                # =========================

                if is_down:

                    try:

                        link = row.locator("a").first()

                        if link.count() > 0:

                            link.click()

                            page.wait_for_timeout(4000)

                            body = page.locator("body").inner_text()

                            marker = "Dernière connexion:"

                            if marker in body:

                                after = body.split(marker)[1]

                                date_str = after.split("\n")[0].strip()

                                try:

                                    dt = datetime.strptime(
                                        date_str,
                                        "%d/%m/%Y %H:%M:%S"
                                    )

                                    last_connection = dt.replace(
                                        tzinfo=PARIS
                                    )

                                except:
                                    pass

                            page.go_back(
                                wait_until="networkidle"
                            )

                            page.wait_for_timeout(4000)

                    except:
                        pass

                # =========================
                # HISTORY
                # =========================

                key = gateway_id

                if key not in history:

                    history[key] = {
                        "down_since": None,
                        "samples": []
                    }

                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not is_down
                })

                # Garde 24h
                recent = []

                for s in history[key]["samples"]:

                    t = datetime.fromisoformat(
                        s["time"]
                    )

                    age = (
                        NOW - t
                    ).total_seconds()

                    if age <= 86400:
                        recent.append(s)

                history[key]["samples"] = recent

                # DOWN SINCE
                if is_down:

                    if (
                        history[key]["down_since"] is None
                        and last_connection
                    ):

                        history[key]["down_since"] = (
                            last_connection.isoformat()
                        )

                else:

                    history[key]["down_since"] = None

                # DUREE HS
                down_hours = 0

                if history[key]["down_since"]:

                    down_start = datetime.fromisoformat(
                        history[key]["down_since"]
                    )

                    down_hours = round(
                        (
                            NOW - down_start
                        ).total_seconds() / 3600,
                        1
                    )

                # SERVICE 24H
                samples = history[key]["samples"]

                service_24h = 0

                if samples:

                    up_count = sum(
                        1 for s in samples if s["up"]
                    )

                    service_24h = round(
                        (
                            up_count / len(samples)
                        ) * 100,
                        1
                    )

                gateways.append({

                    "cluster": cluster["name"],
                    "name": name,
                    "status": status,
                    "gateway_id": gateway_id,
                    "model": model,
                    "connection": connection,
                    "network": network,
                    "firmware": firmware,
                    "city": city,
                    "geolocation": geolocation,
                    "down": is_down,
                    "last_connection": (
                        last_connection.isoformat()
                        if last_connection
                        else None
                    ),
                    "down_since": history[key]["down_since"],
                    "down_hours": down_hours,
                    "maintenance": down_hours >= 24,
                    "service_24h": service_24h
                })

        except Exception as e:

            gateways.append({

                "cluster": cluster["name"],
                "name": "ERREUR",
                "status": "Erreur",
                "gateway_id": "",
                "model": "",
                "connection": str(e),
                "network": "",
                "firmware": "",
                "city": "",
                "geolocation": "",
                "down": True,
                "last_connection": None,
                "down_since": None,
                "down_hours": 0,
                "maintenance": False,
                "service_24h": 0
            })

        page.close()

    browser.close()


# =========================
# SAVE HISTORY
# =========================

with open(HISTORY_FILE, "w", encoding="utf-8") as f:

    json.dump(
        history,
        f,
        indent=2,
        ensure_ascii=False
    )


# =========================
# STATS
# =========================

total = len(gateways)

down = sum(
    1 for g in gateways if g["down"]
)

ok = total - down

maintenance = sum(
    1 for g in gateways if g["maintenance"]
)

service = round(
    (ok / total) * 100,
    1
) if total else 0

clusters = sorted(
    list(
        set(g["cluster"] for g in gateways)
    )
)


# =========================
# HTML
# =========================

html_page = f"""
<!DOCTYPE html>
<html>

<head>

<meta charset="utf-8">

<meta
name="viewport"
content="width=device-width, initial-scale=1"
>

<title>Monitoring Requea</title>

<style>

body {{
    background:#0f172a;
    color:white;
    font-family:Arial;
    margin:0;
    padding:16px;
}}

h1 {{
    margin-bottom:5px;
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
    border-radius:14px;
    padding:16px;
}}

.big {{
    font-size:32px;
    font-weight:bold;
    margin-top:10px;
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

.cluster-buttons {{
    margin:20px 0;
}}

.cluster-buttons button {{
    margin:4px;
    padding:10px 14px;
    border:none;
    border-radius:8px;
    cursor:pointer;
}}

.table-wrap {{
    overflow-x:auto;
    margin-bottom:40px;
}}

table {{
    width:100%;
    min-width:1400px;
    border-collapse:collapse;
    background:#111827;
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

    const rows =
        document.querySelectorAll(".gateway-row")

    rows.forEach(row => {{

        if (
            cluster === "ALL"
            || row.dataset.cluster === cluster
        ) {{
            row.style.display = ""
        }}
        else {{
            row.style.display = "none"
        }}

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

<div class="cluster-buttons">

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
</div>

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

    row_class = (
        "maintenance"
        if g["maintenance"]
        else "down"
    )

    html_page += f"""
<tr
class="gateway-row {row_class}"
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
<td>{fmt_date(g["last_connection"])}</td>
<td>{fmt_date(g["down_since"])}</td>
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

    row_class = ""

    if g["maintenance"]:
        row_class = "maintenance"

    elif g["down"]:
        row_class = "down"

    badge = (
        "ko"
        if g["down"]
        else "ok"
    )

    html_page += f"""
<tr
class="gateway-row {row_class}"
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
