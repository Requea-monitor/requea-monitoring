from playwright.sync_api import sync_playwright
from datetime import datetime, timezone
import os
import json
import html as html_escape

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])
NOW = datetime.now(timezone.utc)
HISTORY_FILE = "history.json"

try:
    with open(HISTORY_FILE, "r", encoding="utf-8") as f:
        history = json.load(f)
except Exception:
    history = {}

gateways = []


def esc(value):
    return html_escape.escape(str(value or ""))


def parse_gateway_row(raw, site_name):
    parts = [
        p.strip()
        for p in raw.replace("\t", "\n").split("\n")
        if p.strip()
    ]

    while parts and parts[0] in ["a", "", "-", "_", "–", "—"]:
        parts = parts[1:]

    if len(parts) < 9:
        return None

    name = parts[0]
    status = parts[1]
    gateway_id = parts[2]
    model = parts[3]
    connection = parts[4]
    operator = parts[5] if len(parts) > 5 else ""
    network = parts[6] if len(parts) > 6 else ""
    firmware = parts[7] if len(parts) > 7 else ""
    city = parts[8] if len(parts) > 8 else ""
    geolocation = parts[9] if len(parts) > 9 else ""

    if status != "Active":
        return None

    conn = connection.lower()

    is_down = (
        "déconnect" in conn
        or "deconnect" in conn
        or "closed" in conn
        or "offline" in conn
        or "down" in conn
    )

    return {
        "cluster": site_name,
        "name": name,
        "status": status,
        "connection": connection,
        "gateway_id": gateway_id,
        "model": model,
        "operator": operator,
        "network": network,
        "firmware": firmware,
        "city": city,
        "geolocation": geolocation,
        "raw": raw,
        "down": is_down
    }


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    for site in CONFIG:
        page = browser.new_page()

        try:
            page.goto(site["url"], wait_until="networkidle", timeout=60000)

            page.fill('input[type="text"]', site["login"])
            page.fill('input[type="password"]', site["password"])
            page.click('button[type="submit"]')

            page.wait_for_timeout(6000)

            page.goto(
                f'{site["url"]}/page/Network_Gateways',
                wait_until="networkidle",
                timeout=60000
            )

            page.wait_for_timeout(10000)

            rows = page.locator("tr")
            count = rows.count()

            for i in range(count):
                raw = rows.nth(i).inner_text().strip()
                gateway = parse_gateway_row(raw, site["name"])

                if not gateway:
                    continue

                key = gateway["gateway_id"]

                if key not in history:
                    history[key] = {
                        "first_seen": NOW.isoformat(),
                        "down_since": None,
                        "samples": []
                    }

                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not gateway["down"]
                })

                recent = []
                for sample in history[key]["samples"]:
                    t = datetime.fromisoformat(sample["time"])
                    age_hours = (NOW - t).total_seconds() / 3600
                    if age_hours <= 24:
                        recent.append(sample)

                history[key]["samples"] = recent

                if gateway["down"]:
                    if history[key]["down_since"] is None:
                        history[key]["down_since"] = NOW.isoformat()
                else:
                    history[key]["down_since"] = None

                down_hours = 0
                if history[key]["down_since"]:
                    down_start = datetime.fromisoformat(history[key]["down_since"])
                    down_hours = round((NOW - down_start).total_seconds() / 3600, 1)

                samples = history[key]["samples"]
                service_24h = 0

                if samples:
                    up_count = sum(1 for s in samples if s["up"])
                    service_24h = round((up_count / len(samples)) * 100, 1)

                gateway["down_since"] = history[key]["down_since"]
                gateway["down_hours"] = down_hours
                gateway["maintenance"] = down_hours >= 24
                gateway["service_24h"] = service_24h

                gateways.append(gateway)

        except Exception as e:
            gateways.append({
                "cluster": site["name"],
                "name": "ERREUR CLUSTER",
                "status": "Erreur",
                "connection": str(e),
                "gateway_id": "",
                "model": "",
                "operator": "",
                "network": "",
                "firmware": "",
                "city": "",
                "geolocation": "",
                "raw": str(e),
                "down": True,
                "down_since": NOW.isoformat(),
                "down_hours": 0,
                "maintenance": True,
                "service_24h": 0
            })

        page.close()

    browser.close()


with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)


total = len(gateways)
down = sum(1 for g in gateways if g["down"])
maintenance = sum(1 for g in gateways if g["maintenance"])
ok = total - down
service = round((ok / total) * 100, 1) if total else 0

gateways_sorted = sorted(
    gateways,
    key=lambda g: (
        not g["maintenance"],
        not g["down"],
        g["cluster"],
        g["name"]
    )
)

html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitoring Requea LoRaWAN</title>

<style>
body {{
    margin:0;
    padding:16px;
    font-family:Arial,sans-serif;
    background:#0f172a;
    color:white;
}}

h1 {{
    font-size:26px;
    margin-bottom:5px;
}}

.cards {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(160px,1fr));
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
    margin-top:8px;
}}

.green {{ color:#22c55e; }}
.red {{ color:#ef4444; }}
.orange {{ color:#f59e0b; }}

.table-wrap {{
    width:100%;
    overflow-x:auto;
    margin-bottom:35px;
}}

table {{
    width:100%;
    min-width:1100px;
    border-collapse:collapse;
    background:#111827;
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
    font-weight:bold;
}}

.badge {{
    padding:5px 9px;
    border-radius:20px;
    display:inline-block;
}}

.ok {{ background:#166534; }}
.ko {{ background:#991b1b; }}
.warn {{ background:#92400e; }}

@media (max-width:700px) {{
    body {{ padding:10px; }}
    h1 {{ font-size:22px; }}
    .big {{ font-size:28px; }}
    table {{ font-size:14px; }}
}}
</style>
</head>

<body>

<h1>📡 Monitoring Requea LoRaWAN</h1>

<p>Dernière mise à jour : {NOW.astimezone().strftime("%d/%m/%Y %H:%M")}</p>

<div class="cards">
  <div class="card">Passerelles Active<div class="big">{total}</div></div>
  <div class="card">Taux service instantané<div class="big green">{service}%</div></div>
  <div class="card">Connectées<div class="big green">{ok}</div></div>
  <div class="card">Défaillantes<div class="big red">{down}</div></div>
  <div class="card">Maintenance > 24h<div class="big orange">{maintenance}</div></div>
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
<th>HS depuis</th>
<th>Durée HS</th>
<th>Service 24h</th>
<th>Maintenance</th>
<th>Firmware</th>
</tr>
"""

for g in gateways_sorted:
    if not g["down"]:
        continue

    cls = "maintenance" if g["maintenance"] else "down"
    maint = "OUI" if g["maintenance"] else "Non"
    badge = "warn" if g["maintenance"] else "ko"

    html += f"""
<tr class="{cls}">
<td>{esc(g["cluster"])}</td>
<td>{esc(g["name"])}</td>
<td>{esc(g["city"])}</td>
<td>{esc(g["geolocation"])}</td>
<td><span class="badge {badge}">{esc(g["connection"])}</span></td>
<td>{esc(g["down_since"])}</td>
<td>{esc(g["down_hours"])} h</td>
<td>{esc(g["service_24h"])}%</td>
<td>{maint}</td>
<td>{esc(g["firmware"])}</td>
</tr>
"""

html += """
</table>
</div>

<h2>📋 Toutes les passerelles Active</h2>

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

for g in gateways_sorted:
    cls = "maintenance" if g["maintenance"] else ("down" if g["down"] else "")
    badge = "ok" if not g["down"] else "ko"

    html += f"""
<tr class="{cls}">
<td>{esc(g["cluster"])}</td>
<td>{esc(g["name"])}</td>
<td>{esc(g["city"])}</td>
<td>{esc(g["geolocation"])}</td>
<td>{esc(g["status"])}</td>
<td><span class="badge {badge}">{esc(g["connection"])}</span></td>
<td>{esc(g["service_24h"])}%</td>
<td>{esc(g["firmware"])}</td>
<td>{esc(g["gateway_id"])}</td>
</tr>
"""

html += """
</table>
</div>

</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open("public/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Dashboard généré avec {total} passerelles Active")
