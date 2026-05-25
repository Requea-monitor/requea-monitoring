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
    return " ".join(str(v or "").replace("\n", " ").split()).strip()


def fmt_date(v):
    if not v:
        return "-"
    try:
        return datetime.fromisoformat(v).astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(v)


def normalize_connection(v):
    t = str(v or "").lower()

    if "déconnect" in t or "deconnect" in t or "closed" in t or "offline" in t:
        return "Déconnectée", True

    if "connectée" in t or "connectee" in t or "connected" in t:
        return "Connectée", False

    return clean(v), False


def parse_last_connection(text):
    m = re.search(
        r"Dernière connexion\s*:\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text
    )

    if not m:
        return None

    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
    except Exception:
        return None


def geoloc_from_text(text):
    m = re.search(r"([0-9]{2}\.[0-9]+)[,\s]+([0-9]{1,2}\.[0-9]+)", text)
    if not m:
        return ""
    return f"{m.group(1)}, {m.group(2)}"


def login(page, cluster):
    page.goto(cluster["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(3000)

    password = page.locator('input[type="password"]:visible').first
    username = page.locator('input:visible:not([type="password"]):not([type="hidden"])').first

    username.fill(cluster["login"])
    password.fill(cluster["password"])

    page.wait_for_timeout(500)

    password.press("Enter")
    page.wait_for_timeout(10000)

    body = page.locator("body").inner_text()

    if "Mot de passe oublié" in body or "Forgot your password" in body:
        raise Exception("Connexion refusée")


def parse_gateway(values, raw, cluster_name):
    if "Active" not in values:
        return None

    idx = values.index("Active")

    name = values[idx - 1] if idx > 0 else ""
    status = "Active"

    gateway_id = ""
    for v in values:
        if re.fullmatch(r"[0-9A-Fa-f]{12,32}", v):
            gateway_id = v
            break

    connection_raw = ""
    for v in values:
        low = v.lower()
        if "connect" in low or "closed" in low or "offline" in low:
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

    network = ""
    for v in values:
        if "lorawan" in v.lower() or "requea" in v.lower():
            network = v
            break

    geolocation = geoloc_from_text(raw)

    city = ""
    if firmware and firmware in values:
        fidx = values.index(firmware)
        if len(values) > fidx + 1:
            city = values[fidx + 1]

    if not city:
        for v in reversed(values):
            if (
                v
                and v not in [name, status, gateway_id, model, connection_raw, network, firmware]
                and not re.search(r"[0-9]{2}\.[0-9]+", v)
            ):
                city = v
                break

    if not gateway_id:
        gateway_id = f"{cluster_name}-{name}"

    return {
        "cluster": cluster_name,
        "name": name or gateway_id,
        "status": status,
        "gateway_id": gateway_id,
        "model": model,
        "connection": connection,
        "network": network,
        "firmware": firmware,
        "city": city,
        "geolocation": geolocation,
        "down": is_down,
        "last_connection": None
    }


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    for cluster in CONFIG:
        page = browser.new_page()

        try:
            login(page, cluster)

            page.goto(
                f'{cluster["url"]}/page/Network_Gateways',
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(15000)

            rows = page.locator("tr")
            count = rows.count()

            for i in range(count):
                row = rows.nth(i)
                raw = clean(row.inner_text())

                if "Active" not in raw:
                    continue

                cells = row.locator("td")

                values = [
                    clean(cells.nth(j).inner_text())
                    for j in range(cells.count())
                ]

                values = [
                    v for v in values
                    if v and v not in [">", "›"]
                ]

                gateway = parse_gateway(values, raw, cluster["name"])

                if not gateway:
                    continue

                if gateway["down"]:
                    try:
                        link = row.locator("a").first
                        link.click()
                        page.wait_for_timeout(5000)

                        detail = page.locator("body").inner_text()
                        last = parse_last_connection(detail)

                        if last:
                            gateway["last_connection"] = last.isoformat()

                        page.go_back(wait_until="domcontentloaded")
                        page.wait_for_timeout(7000)
                        rows = page.locator("tr")

                    except Exception:
                        pass

                key = gateway["gateway_id"]

                if key not in history:
                    history[key] = {
                        "down_since": None,
                        "samples": []
                    }

                if gateway["down"]:
                    if gateway["last_connection"]:
                        history[key]["down_since"] = gateway["last_connection"]
                    elif not history[key]["down_since"]:
                        history[key]["down_since"] = NOW.isoformat()
                else:
                    history[key]["down_since"] = None

                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not gateway["down"]
                })

                history[key]["samples"] = [
                    s for s in history[key]["samples"]
                    if (NOW - datetime.fromisoformat(s["time"])).total_seconds() <= 86400
                ]

                samples = history[key]["samples"]

                service_24h = 0
                if samples:
                    service_24h = round(
                        sum(1 for s in samples if s["up"]) / len(samples) * 100,
                        1
                    )

                down_hours = 0
                if history[key]["down_since"]:
                    start = datetime.fromisoformat(history[key]["down_since"])
                    down_hours = round((NOW - start).total_seconds() / 3600, 1)

                gateway["down_since"] = history[key]["down_since"]
                gateway["down_hours"] = down_hours
                gateway["service_24h"] = service_24h
                gateway["maintenance"] = down_hours >= 24

                gateways.append(gateway)

        except Exception as e:
            gateways.append({
                "cluster": cluster["name"],
                "name": "ERREUR CONNEXION",
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
                "service_24h": 0,
                "maintenance": False
            })

        page.close()

    browser.close()


with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)


total = len([g for g in gateways if g["status"] == "Active"])
down = len([g for g in gateways if g["down"] and g["status"] == "Active"])
ok = total - down
maintenance = len([g for g in gateways if g.get("maintenance") and g["status"] == "Active"])
service = round(ok / total * 100, 1) if total else 0

clusters = sorted(set(g["cluster"] for g in gateways))

html_page = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
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
    grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
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

.green {{ color:#22c55e; }}
.red {{ color:#ef4444; }}
.orange {{ color:#f59e0b; }}

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

.ok {{ background:#166534; }}
.ko {{ background:#991b1b; }}
</style>

<script>
function filterCluster(cluster) {{
    document.querySelectorAll(".gateway-row").forEach(row => {{
        row.style.display =
            cluster === "ALL" || row.dataset.cluster === cluster
            ? ""
            : "none";
    }});
}}
</script>
</head>

<body>

<h1>📡 Monitoring Requea LoRaWAN</h1>
<p>Dernière mise à jour : {NOW.strftime("%d/%m/%Y %H:%M")}</p>

<div class="cards">
<div class="card">Clusters<div class="big">{len(clusters)}</div></div>
<div class="card">Passerelles Active<div class="big">{total}</div></div>
<div class="card">Taux service instantané<div class="big green">{service}%</div></div>
<div class="card">Connectées<div class="big green">{ok}</div></div>
<div class="card">Défaillantes<div class="big red">{down}</div></div>
<div class="card">Maintenance >24h<div class="big orange">{maintenance}</div></div>
</div>

<h2>🌍 Clusters</h2>
<button onclick="filterCluster('ALL')">Tous</button>
"""

for c in clusters:
    html_page += f"""
<button onclick="filterCluster('{esc(c)}')">{esc(c)}</button>
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

    row_class = "maintenance" if g.get("maintenance") else "down"

    html_page += f"""
<tr class="gateway-row {row_class}" data-cluster="{esc(g["cluster"])}">
<td>{esc(g["cluster"])}</td>
<td>{esc(g["name"])}</td>
<td>{esc(g["city"])}</td>
<td>{esc(g["geolocation"])}</td>
<td><span class="badge ko">{esc(g["connection"])}</span></td>
<td>{fmt_date(g["last_connection"])}</td>
<td>{fmt_date(g["down_since"])}</td>
<td>{g["down_hours"]} h</td>
<td>{g["service_24h"]}%</td>
<td>{"OUI" if g.get("maintenance") else "Non"}</td>
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
    badge = "ko" if g["down"] else "ok"
    row_class = "maintenance" if g.get("maintenance") else ("down" if g["down"] else "")

    html_page += f"""
<tr class="gateway-row {row_class}" data-cluster="{esc(g["cluster"])}">
<td>{esc(g["cluster"])}</td>
<td>{esc(g["name"])}</td>
<td>{esc(g["city"])}</td>
<td>{esc(g["geolocation"])}</td>
<td>{esc(g["status"])}</td>
<td><span class="badge {badge}">{esc(g["connection"])}</span></td>
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

with open("public/index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Dashboard généré : {total} passerelles actives")
