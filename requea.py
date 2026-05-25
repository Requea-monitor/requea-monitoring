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

with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    for site in CONFIG:
        page = browser.new_page()

        try:
            page.goto(site["url"], wait_until="networkidle")

            page.fill('input[type="text"]', site["login"])
            page.fill('input[type="password"]', site["password"])
            page.click('button[type="submit"]')

            page.wait_for_timeout(5000)

            page.goto(f'{site["url"]}/page/Network_Gateways', wait_until="networkidle")
            page.wait_for_timeout(8000)

            rows = page.locator("tr")
            count = rows.count()

            for i in range(count):
                row = rows.nth(i)
                cols = row.locator("td")

                values = []
                for j in range(cols.count()):
                    txt = cols.nth(j).inner_text().strip()
                    if txt:
                        values.append(txt)

                if len(values) < 8:
                    continue

                # Détection ligne passerelle
                joined = " ".join(values)

                if "Active" not in values:
                    continue

                if "Connectée" not in joined and "Déconnectée" not in joined:
                    continue

                gateway = {
                    "cluster": site["name"],
                    "name": values[1] if len(values) > 1 else "",
                    "status": values[2] if len(values) > 2 else "",
                    "gateway_id": values[3] if len(values) > 3 else "",
                    "model": values[4] if len(values) > 4 else "",
                    "connection": values[5] if len(values) > 5 else "",
                    "operator": values[6] if len(values) > 6 else "",
                    "network": values[7] if len(values) > 7 else "",
                    "firmware": values[8] if len(values) > 8 else "",
                    "city": values[9] if len(values) > 9 else "",
                    "gps": values[10] if len(values) > 10 else "",
                }

                gateway["down"] = gateway["connection"] != "Connectée"

                key = gateway["gateway_id"] or f'{gateway["cluster"]}-{gateway["name"]}'

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
                if samples:
                    up_count = sum(1 for s in samples if s["up"])
                    service_24h = round((up_count / len(samples)) * 100, 1)
                else:
                    service_24h = 0

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
                "firmware": "",
                "city": "",
                "gps": "",
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

def esc(value):
    return html_escape.escape(str(value or ""))

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
    padding:20px;
    font-family:Arial, sans-serif;
    background:#0f172a;
    color:white;
}}
h1 {{ margin-bottom:5px; }}
.cards {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
    gap:15px;
    margin:25px 0;
}}
.card {{
    background:#1e293b;
    padding:18px;
    border-radius:14px;
}}
.big {{
    font-size:34px;
    font-weight:bold;
    margin-top:8px;
}}
.green {{ color:#22c55e; }}
.red {{ color:#ef4444; }}
.orange {{ color:#f59e0b; }}
table {{
    width:100%;
    border-collapse:collapse;
    background:#111827;
    margin-bottom:35px;
}}
th {{
    background:#334155;
    padding:10px;
    text-align:left;
    position:sticky;
    top:0;
}}
td {{
    padding:10px;
    border-bottom:1px solid #334155;
}}
tr.down {{ background:#451a1a; }}
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

<table>
<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
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

<h2>📋 Toutes les passerelles Active</h2>

<table>
<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
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
<td>{esc(g["status"])}</td>
<td><span class="badge {badge}">{esc(g["connection"])}</span></td>
<td>{esc(g["service_24h"])}%</td>
<td>{esc(g["firmware"])}</td>
<td>{esc(g["gateway_id"])}</td>
</tr>
"""

html += """
</table>
</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open("public/index.html", "w", encoding="utf-8") as f:
    f.write(html)

print(f"Dashboard généré avec {total} passerelles Active")
