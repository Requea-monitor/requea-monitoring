from playwright.sync_api import sync_playwright
from bs4 import BeautifulSoup
from datetime import datetime
import os
import json

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

SITE = CONFIG[0]

URL = SITE["url"]
LOGIN = SITE["login"]
PASSWORD = SITE["password"]

all_gateways = []

with sync_playwright() as p:

    browser = p.chromium.launch(headless=True)

    page = browser.new_page()

    # LOGIN
    page.goto(URL)

    page.fill('input[type="text"]', LOGIN)
    page.fill('input[type="password"]', PASSWORD)

    page.click('button[type="submit"]')

    page.wait_for_timeout(5000)

    # PAGE GATEWAYS
    page.goto(f"{URL}/page/Network_Gateways")

    page.wait_for_timeout(5000)

    html = page.content()

    browser.close()

# PARSE HTML
soup = BeautifulSoup(html, "html.parser")

rows = soup.find_all("tr")

for row in rows:

    cols = row.find_all("td")

    if len(cols) < 6:
        continue

    try:

        values = [c.get_text(strip=True) for c in cols]

        name = values[1] if len(values) > 1 else ""
        status = values[2] if len(values) > 2 else ""
        gateway_id = values[3] if len(values) > 3 else ""
        model = values[4] if len(values) > 4 else ""
        connection = values[5] if len(values) > 5 else ""
        firmware = values[7] if len(values) > 7 else ""
        city = values[-1] if len(values) > 8 else ""

        down = False

        txt = f"{status} {connection}".lower()

        if (
            "déconnect" in txt
            or "inactive" in txt
            or "maintenance" in txt
            or "depose" in txt
            or "dépos" in txt
            or "offline" in txt
            or "down" in txt
        ):
            down = True

        gateway = {
            "name": name,
            "status": status,
            "connection": connection,
            "gateway_id": gateway_id,
            "model": model,
            "firmware": firmware,
            "city": city,
            "down": down
        }

        all_gateways.append(gateway)

    except:
        pass

# STATS
total = len(all_gateways)
down_count = len([g for g in all_gateways if g["down"]])
up_count = total - down_count

availability = 0

if total > 0:
    availability = round((up_count / total) * 100, 2)

# HTML
html = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">

<title>Requea Monitoring</title>

<style>

body {{
    background:#0f172a;
    color:white;
    font-family:Arial;
    margin:0;
    padding:20px;
}}

h1 {{
    margin-bottom:10px;
}}

.stats {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
    gap:20px;
    margin-bottom:30px;
}}

.card {{
    background:#1e293b;
    padding:20px;
    border-radius:15px;
}}

.big {{
    font-size:42px;
    margin-top:10px;
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
    border-collapse:collapse;
    margin-top:20px;
}}

th {{
    background:#1e293b;
    padding:12px;
    text-align:left;
}}

td {{
    padding:12px;
    border-bottom:1px solid #334155;
}}

tr.down {{
    background:#450a0a;
}}

.badge-ok {{
    background:#166534;
    padding:6px 10px;
    border-radius:20px;
}}

.badge-down {{
    background:#991b1b;
    padding:6px 10px;
    border-radius:20px;
}}

</style>
</head>

<body>

<h1>📡 Requea Monitoring</h1>

<p>Dernière mise à jour : {datetime.now()}</p>

<div class="stats">

<div class="card">
<div>Total passerelles</div>
<div class="big">{total}</div>
</div>

<div class="card">
<div>Taux de service</div>
<div class="big green">{availability}%</div>
</div>

<div class="card">
<div>Passerelles OK</div>
<div class="big green">{up_count}</div>
</div>

<div class="card">
<div>Défaillantes</div>
<div class="big red">{down_count}</div>
</div>

</div>

<h2>🚨 Passerelles en défaut</h2>

<table>

<tr>
<th>Nom</th>
<th>Ville</th>
<th>Status</th>
<th>Connexion</th>
<th>Firmware</th>
</tr>
"""

for g in all_gateways:

    if g["down"]:

        html += f"""
        <tr class="down">
            <td>{g['name']}</td>
            <td>{g['city']}</td>
            <td><span class="badge-down">{g['status']}</span></td>
            <td>{g['connection']}</td>
            <td>{g['firmware']}</td>
        </tr>
        """

html += """
</table>

<h2>📋 Toutes les passerelles</h2>

<table>

<tr>
<th>Nom</th>
<th>Ville</th>
<th>Status</th>
<th>Connexion</th>
<th>Firmware</th>
<th>ID</th>
</tr>
"""

for g in all_gateways:

    badge = "badge-ok"
    row = ""

    if g["down"]:
        badge = "badge-down"
        row = "down"

    html += f"""
    <tr class="{row}">
        <td>{g['name']}</td>
        <td>{g['city']}</td>
        <td><span class="{badge}">{g['status']}</span></td>
        <td>{g['connection']}</td>
        <td>{g['firmware']}</td>
        <td>{g['gateway_id']}</td>
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

print("Dashboard généré")
