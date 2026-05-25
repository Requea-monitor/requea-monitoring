from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import os, json

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])
NOW = datetime.now(timezone.utc)

HISTORY_FILE = "history.json"

try:
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
except:
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
            page.wait_for_timeout(5000)

            soup = BeautifulSoup(page.content(), "html.parser")

            for row in soup.find_all("tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]

                if len(cols) < 10:
                    continue

                gateway = {
                    "cluster": site["name"],
                    "name": cols[1],
                    "status": cols[2],
                    "gateway_id": cols[3],
                    "model": cols[4],
                    "connection": cols[5],
                    "operator": cols[6],
                    "network": cols[7],
                    "firmware": cols[8],
                    "city": cols[9],
                    "gps": cols[10] if len(cols) > 10 else ""
                }

                # On garde UNIQUEMENT les passerelles Active
                if gateway["status"] != "Active":
                    continue

                gateway["down"] = gateway["connection"] != "Connectée"

                key = gateway["gateway_id"]

                if key not in history:
                    history[key] = {
                        "first_seen": NOW.isoformat(),
                        "down_since": None,
                        "samples": []
                    }

                # Historique des états
                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not gateway["down"]
                })

                # Garde uniquement les 24 dernières heures
                recent = []
                for s in history[key]["samples"]:
                    t = datetime.fromisoformat(s["time"])
                    hours = (NOW - t).total_seconds() / 3600
                    if hours <= 24:
                        recent.append(s)

                history[key]["samples"] = recent

                # Début / fin incident
                if gateway["down"]:
                    if history[key]["down_since"] is None:
                        history[key]["down_since"] = NOW.isoformat()
                else:
                    history[key]["down_since"] = None

                # Durée indispo
                down_hours = 0
                if history[key]["down_since"]:
                    down_start = datetime.fromisoformat(history[key]["down_since"])
                    down_hours = round((NOW - down_start).total_seconds() / 3600, 1)

                gateway["down_since"] = history[key]["down_since"]
                gateway["down_hours"] = down_hours
                gateway["maintenance"] = down_hours >= 24

                samples = history[key]["samples"]
                if samples:
                    up_count = sum(1 for s in samples if s["up"])
                    gateway["service_24h"] = round((up_count / len(samples)) * 100, 1)
                else:
                    gateway["service_24h"] = 0

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
                "down_hours": 0,
                "maintenance": True,
                "service_24h": 0
            })

        page.close()

    browser.close()

with open(HISTORY_FILE, "w") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)

total = len(gateways)
down = sum(1 for g in gateways if g["down"])
maintenance = sum(1 for g in gateways if g["maintenance"])
ok = total - down
service = round((ok / total) * 100, 1) if total else 0

gateways_sorted = sorted(gateways, key=lambda g: (not g["maintenance"], not g["down"], g["cluster"], g["name"]))

html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitoring Requea</title>
<style>
body {{ margin:0; padding:20px; font-family:Arial; background:#0f172a; color:white; }}
h1 {{ margin-bottom:5px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:15px; margin:25px 0; }}
.card {{ background:#1e293b; padding:18px; border-radius:14px; }}
.big {{ font-size:34px; font-weight:bold; margin-top:8px; }}
.green {{ color:#22c55e; }}
.red {{ color:#ef4444; }}
.orange {{ color:#f59e0b; }}
table {{ width:100%; border-collapse:collapse; background:#111827; }}
th {{ background:#334155; padding:10px; text-align:left; }}
td {{ padding:10px; border-bottom:1px solid #334155; }}
tr.down {{ background:#451a1a; }}
tr.maintenance {{ background:#7f1d1d; font-weight:bold; }}
.badge {{ padding:5px 9px; border-radius:20px; }}
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
<th>Cluster</th><th>Passerelle</th><th>Ville</th><th>Connexion</th><th>HS depuis</th><th>Durée HS</th><th>Service 24h</th><th>Maintenance</th><th>Firmware</th>
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
<td>{g["cluster"]}</td>
<td>{g["name"]}</td>
<td>{g["city"]}</td>
<td><span class="badge {badge}">{g["connection"]}</span></td>
<td>{g["down_since"] or ""}</td>
<td>{g["down_hours"]} h</td>
<td>{g["service_24h"]}%</td>
<td>{maint}</td>
<td>{g["firmware"]}</td>
</tr>
"""

html += """
</table>

<h2>📋 Toutes les passerelles Active</h2>

<table>
<tr>
<th>Cluster</th><th>Passerelle</th><th>Ville</th><th>Statut</th><th>Connexion</th><th>Service 24h</th><th>Firmware</th><th>ID</th>
</tr>
"""

for g in gateways_sorted:
    cls = "maintenance" if g["maintenance"] else ("down" if g["down"] else "")
    badge = "ok" if not g["down"] else "ko"

    html += f"""
<tr class="{cls}">
<td>{g["cluster"]}</td>
<td>{g["name"]}</td>
<td>{g["city"]}</td>
<td>{g["status"]}</td>
<td><span class="badge {badge}">{g["connection"]}</span></td>
<td>{g["service_24h"]}%</td>
<td>{g["firmware"]}</td>
<td>{g["gateway_id"]}</td>
</tr>
"""

html += """
</table>
</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open("public/index.html", "w") as f:
    f.write(html)from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime, timezone
import os, json

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])
NOW = datetime.now(timezone.utc)

HISTORY_FILE = "history.json"

try:
    with open(HISTORY_FILE, "r") as f:
        history = json.load(f)
except:
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
            page.wait_for_timeout(5000)

            soup = BeautifulSoup(page.content(), "html.parser")

            for row in soup.find_all("tr"):
                cols = [c.get_text(" ", strip=True) for c in row.find_all("td")]

                if len(cols) < 10:
                    continue

                gateway = {
                    "cluster": site["name"],
                    "name": cols[1],
                    "status": cols[2],
                    "gateway_id": cols[3],
                    "model": cols[4],
                    "connection": cols[5],
                    "operator": cols[6],
                    "network": cols[7],
                    "firmware": cols[8],
                    "city": cols[9],
                    "gps": cols[10] if len(cols) > 10 else ""
                }

                # On garde UNIQUEMENT les passerelles Active
                if gateway["status"] != "Active":
                    continue

                gateway["down"] = gateway["connection"] != "Connectée"

                key = gateway["gateway_id"]

                if key not in history:
                    history[key] = {
                        "first_seen": NOW.isoformat(),
                        "down_since": None,
                        "samples": []
                    }

                # Historique des états
                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not gateway["down"]
                })

                # Garde uniquement les 24 dernières heures
                recent = []
                for s in history[key]["samples"]:
                    t = datetime.fromisoformat(s["time"])
                    hours = (NOW - t).total_seconds() / 3600
                    if hours <= 24:
                        recent.append(s)

                history[key]["samples"] = recent

                # Début / fin incident
                if gateway["down"]:
                    if history[key]["down_since"] is None:
                        history[key]["down_since"] = NOW.isoformat()
                else:
                    history[key]["down_since"] = None

                # Durée indispo
                down_hours = 0
                if history[key]["down_since"]:
                    down_start = datetime.fromisoformat(history[key]["down_since"])
                    down_hours = round((NOW - down_start).total_seconds() / 3600, 1)

                gateway["down_since"] = history[key]["down_since"]
                gateway["down_hours"] = down_hours
                gateway["maintenance"] = down_hours >= 24

                samples = history[key]["samples"]
                if samples:
                    up_count = sum(1 for s in samples if s["up"])
                    gateway["service_24h"] = round((up_count / len(samples)) * 100, 1)
                else:
                    gateway["service_24h"] = 0

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
                "down_hours": 0,
                "maintenance": True,
                "service_24h": 0
            })

        page.close()

    browser.close()

with open(HISTORY_FILE, "w") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)

total = len(gateways)
down = sum(1 for g in gateways if g["down"])
maintenance = sum(1 for g in gateways if g["maintenance"])
ok = total - down
service = round((ok / total) * 100, 1) if total else 0

gateways_sorted = sorted(gateways, key=lambda g: (not g["maintenance"], not g["down"], g["cluster"], g["name"]))

html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitoring Requea</title>
<style>
body {{ margin:0; padding:20px; font-family:Arial; background:#0f172a; color:white; }}
h1 {{ margin-bottom:5px; }}
.cards {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(180px,1fr)); gap:15px; margin:25px 0; }}
.card {{ background:#1e293b; padding:18px; border-radius:14px; }}
.big {{ font-size:34px; font-weight:bold; margin-top:8px; }}
.green {{ color:#22c55e; }}
.red {{ color:#ef4444; }}
.orange {{ color:#f59e0b; }}
table {{ width:100%; border-collapse:collapse; background:#111827; }}
th {{ background:#334155; padding:10px; text-align:left; }}
td {{ padding:10px; border-bottom:1px solid #334155; }}
tr.down {{ background:#451a1a; }}
tr.maintenance {{ background:#7f1d1d; font-weight:bold; }}
.badge {{ padding:5px 9px; border-radius:20px; }}
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
<th>Cluster</th><th>Passerelle</th><th>Ville</th><th>Connexion</th><th>HS depuis</th><th>Durée HS</th><th>Service 24h</th><th>Maintenance</th><th>Firmware</th>
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
<td>{g["cluster"]}</td>
<td>{g["name"]}</td>
<td>{g["city"]}</td>
<td><span class="badge {badge}">{g["connection"]}</span></td>
<td>{g["down_since"] or ""}</td>
<td>{g["down_hours"]} h</td>
<td>{g["service_24h"]}%</td>
<td>{maint}</td>
<td>{g["firmware"]}</td>
</tr>
"""

html += """
</table>

<h2>📋 Toutes les passerelles Active</h2>

<table>
<tr>
<th>Cluster</th><th>Passerelle</th><th>Ville</th><th>Statut</th><th>Connexion</th><th>Service 24h</th><th>Firmware</th><th>ID</th>
</tr>
"""

for g in gateways_sorted:
    cls = "maintenance" if g["maintenance"] else ("down" if g["down"] else "")
    badge = "ok" if not g["down"] else "ko"

    html += f"""
<tr class="{cls}">
<td>{g["cluster"]}</td>
<td>{g["name"]}</td>
<td>{g["city"]}</td>
<td>{g["status"]}</td>
<td><span class="badge {badge}">{g["connection"]}</span></td>
<td>{g["service_24h"]}%</td>
<td>{g["firmware"]}</td>
<td>{g["gateway_id"]}</td>
</tr>
"""

html += """
</table>
</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open("public/index.html", "w") as f:
    f.write(html)
