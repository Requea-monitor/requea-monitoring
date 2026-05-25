import os
import json
from datetime import datetime
from playwright.sync_api import sync_playwright

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

all_gateways = []

with sync_playwright() as p:

    browser = p.chromium.launch(headless=True)

    for site in CONFIG:

        page = browser.new_page()

        try:

            page.goto(site["url"], wait_until="networkidle")

            page.fill('input[type="text"]', site["login"])
            page.fill('input[type="password"]', site["password"])

            page.click('button[type="submit"]')

            page.wait_for_load_state("networkidle")

            page.goto(
                f'{site["url"]}/page/Network_Gateways',
                wait_until="networkidle"
            )

            page.wait_for_timeout(5000)

            rows = page.locator("tr")

            count = rows.count()

            for i in range(count):

                txt = rows.nth(i).inner_text()

                if "Connectée" in txt or "Déconnectée" in txt:

                    status = "OK"

                    if "Déconnectée" in txt:
                        status = "MAINTENANCE"

                    gateway = {
                        "cluster": site["name"],
                        "status": status,
                        "details": txt
                    }

                    all_gateways.append(gateway)

        except Exception as e:

            all_gateways.append({
                "cluster": site["name"],
                "status": "ERREUR",
                "details": str(e)
            })

        page.close()

    browser.close()

updated = datetime.now().strftime("%d/%m/%Y %H:%M")

critical = sum(1 for g in all_gateways if g["status"] == "MAINTENANCE")

html = f"""
<!DOCTYPE html>
<html>
<head>

<meta charset="utf-8">

<title>Monitoring Requea</title>

<style>

body {{
    font-family: Arial;
    margin: 20px;
    background: #f5f7fa;
}}

h1 {{
    margin-bottom: 10px;
}}

.summary {{
    background: white;
    padding: 20px;
    border-radius: 10px;
    margin-bottom: 20px;
}}

.alert {{
    background: #ffdddd;
    color: #900;
    padding: 15px;
    border-radius: 10px;
    margin-bottom: 20px;
    font-size: 18px;
}}

table {{
    width: 100%;
    border-collapse: collapse;
    background: white;
}}

th {{
    background: #222;
    color: white;
    padding: 12px;
}}

td {{
    padding: 10px;
    border-bottom: 1px solid #ddd;
}}

.good {{
    background: #e8ffe8;
}}

.bad {{
    background: #ffe5e5;
}}

.ok {{
    color: green;
    font-weight: bold;
}}

.ko {{
    color: red;
    font-weight: bold;
}}

</style>

</head>

<body>

<h1>Monitoring Requea LoRaWAN</h1>

<div class="summary">
Dernière mise à jour : {updated}
</div>

<div class="alert">
Passerelles nécessitant une maintenance : {critical}
</div>

<table>

<tr>
<th>Cluster</th>
<th>Statut</th>
<th>Détails</th>
</tr>
"""

for g in all_gateways:

    row = "good"

    if g["status"] != "OK":
        row = "bad"

    color = "ok"

    if g["status"] != "OK":
        color = "ko"

    html += f"""
<tr class="{row}">
<td>{g["cluster"]}</td>
<td class="{color}">{g["status"]}</td>
<td>{g["details"]}</td>
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
