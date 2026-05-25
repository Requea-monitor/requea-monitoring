from playwright.sync_api import sync_playwright
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import os
import json
import html as html_escape
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
    return html_escape.escape(str(v or ""))


def parse_last_connection(text):
    m = re.search(r"Dernière connexion\s*:\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})", text)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
    except Exception:
        return None


def clean_cell(txt):
    return " ".join(txt.replace("\n", " ").split()).strip()


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

            page.goto(f'{site["url"]}/page/Network_Gateways', wait_until="networkidle", timeout=60000)
            page.wait_for_timeout(10000)

            rows_count = page.locator("tr").count()

            for i in range(rows_count):
                rows = page.locator("tr")
                row = rows.nth(i)
                cells = row.locator("td")

                values = [clean_cell(cells.nth(j).inner_text()) for j in range(cells.count())]

                if "Active" not in values:
                    continue

                status_idx = values.index("Active")

                name = values[status_idx - 1] if status_idx >= 1 else ""
                status = values[status_idx]
                gateway_id = values[status_idx + 1] if len(values) > status_idx + 1 else ""
                model = values[status_idx + 2] if len(values) > status_idx + 2 else ""
                connection = values[status_idx + 3] if len(values) > status_idx + 3 else ""
                network = values[status_idx + 5] if len(values) > status_idx + 5 else ""
                firmware = values[status_idx + 6] if len(values) > status_idx + 6 else ""
                city = values[status_idx + 7] if len(values) > status_idx + 7 else ""
                geolocation = values[status_idx + 8] if len(values) > status_idx + 8 else ""

                conn_lower = connection.lower()

                is_down = (
                    "déconnect" in conn_lower
                    or "deconnect" in conn_lower
                    or "closed" in conn_lower
                    or "offline" in conn_lower
                )

                last_connection = None

                try:
                    link = row.locator("a").first()
                    if link.count() > 0:
                        link.click()
                        page.wait_for_timeout(4000)

                        detail_text = page.locator("body").inner_text()
                        last_connection = parse_last_connection(detail_text)

                        page.go_back(wait_until="networkidle")
                        page.wait_for_timeout(4000)
                except Exception:
                    pass

                key = gateway_id or f'{site["name"]}-{name}'

                if key not in history:
                    history[key] = {
                        "first_seen": NOW.isoformat(),
                        "down_since": None,
                        "samples": []
                    }

                if is_down:
                    if last_connection:
                        history[key]["down_since"] = last_connection.isoformat()
                    elif history[key]["down_since"] is None:
                        history[key]["down_since"] = NOW.isoformat()
                else:
                    history[key]["down_since"] = None

                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not is_down
                })

                recent = []
                for s in history[key]["samples"]:
                    t = datetime.fromisoformat(s["time"])
                    if (NOW - t).total_seconds() <= 24 * 3600:
                        recent.append(s)

                history[key]["samples"] = recent

                down_hours = 0
                if history[key]["down_since"]:
                    down_start = datetime.fromisoformat(history[key]["down_since"])
                    down_hours = round((NOW - down_start).total_seconds() / 3600, 1)

                samples = history[key]["samples"]
                service_24h = 0
                if samples:
                    service_24h = round((sum(1 for s in samples if s["up"]) / len(samples)) * 100, 1)

                gateways.append({
                    "cluster": site["name"],
                    "name": name or gateway_id,
                    "status": status,
                    "connection": connection,
                    "gateway_id": gateway_id,
                    "model": model,
                    "network": network,
                    "firmware": firmware,
                    "city": city,
                    "geolocation": geolocation,
                    "last_connection": last_connection.isoformat() if last_connection else "",
                    "down": is_down,
                    "down_since": history[key]["down_since"],
                    "down_hours": down_hours,
                    "maintenance": down_hours >= 24,
                    "service_24h": service_24h
                })

        except Exception as e:
            gateways.append({
                "cluster": site["name"],
                "name": "ERREUR CLUSTER",
                "status": "Erreur",
                "connection": str(e),
                "gateway_id": "",
                "model": "",
                "network": "",
                "firmware": "",
                "city": "",
                "geolocation": "",
                "last_connection": "",
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
h1 {{ font-size:26px; }}
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
    min-width:1250px;
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
<p>Dernière mise à jour : {NOW.strftime("%d/%m/%Y %H:%M")}</p>

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
<th>Dernière connexion</th>
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
<td>{esc(g["last_connection"])}</td>
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
<th>Dernière connexion</th>
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
<td>{esc(g["last_connection"])}</td>
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

print(f"Dashboard généré avec {total} passerelles Active dont {down} défaillante(s)")
