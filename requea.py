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
except:
    history = {}

gateways = []


def esc(v):
    return html_escape.escape(str(v or ""))


def clean(txt):
    return txt.replace("\n", " ").replace("\t", " ").strip()


def get_last_connection(page):

    try:

        txt = page.locator("text=Dernière connexion").first.inner_text()

        if ":" in txt:
            return txt.split(":", 1)[1].strip()

    except:
        pass

    return "-"


with sync_playwright() as p:

    browser = p.chromium.launch(headless=True)

    for site in CONFIG:

        page = browser.new_page()

        try:

            page.goto(
                site["url"],
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(3000)

            login_inputs = page.locator('input[type="text"]')

            password_inputs = page.locator('input[type="password"]')

            if login_inputs.count() == 0:
                raise Exception("Champ login introuvable")

            login_inputs.first.fill(site["login"])

            password_inputs.first.fill(site["password"])

            page.click('button[type="submit"]')

            page.wait_for_timeout(6000)

            page.goto(
                f'{site["url"]}/page/Network_Gateways',
                wait_until="networkidle",
                timeout=60000
            )

            page.wait_for_timeout(5000)

            rows = page.locator("tbody tr")

            count = rows.count()

            for i in range(count):

                row = rows.nth(i)

                cols = row.locator("td")

                if cols.count() < 8:
                    continue

                try:

                    name = clean(cols.nth(0).inner_text())
                    status = clean(cols.nth(1).inner_text())
                    gateway_id = clean(cols.nth(2).inner_text())
                    model = clean(cols.nth(3).inner_text())
                    connection = clean(cols.nth(4).inner_text())
                    firmware = clean(cols.nth(7).inner_text())
                    city = clean(cols.nth(8).inner_text())
                    geo = clean(cols.nth(9).inner_text())

                except:
                    continue

                if status != "Active":
                    continue

                down = (
                    "Déconnectée" in connection
                    or "Closed" in connection
                )

                connection_label = (
                    "Déconnectée"
                    if down
                    else "Connectée"
                )

                last_connection = "-"

                if down:

                    try:

                        row.click()

                        page.wait_for_timeout(3000)

                        last_connection = get_last_connection(page)

                        page.goto(
                            f'{site["url"]}/page/Network_Gateways',
                            wait_until="networkidle",
                            timeout=60000
                        )

                        page.wait_for_timeout(3000)

                        rows = page.locator("tbody tr")

                    except:
                        pass

                key = gateway_id

                if key not in history:

                    history[key] = {
                        "first_seen": NOW.isoformat(),
                        "down_since": None,
                        "samples": []
                    }

                history[key]["samples"].append({
                    "time": NOW.isoformat(),
                    "up": not down
                })

                recent = []

                for sample in history[key]["samples"]:

                    t = datetime.fromisoformat(sample["time"])

                    age = (
                        NOW - t
                    ).total_seconds() / 3600

                    if age <= 24:
                        recent.append(sample)

                history[key]["samples"] = recent

                if down:

                    if history[key]["down_since"] is None:
                        history[key]["down_since"] = NOW.isoformat()

                else:

                    history[key]["down_since"] = None

                down_hours = 0

                if history[key]["down_since"]:

                    d = datetime.fromisoformat(
                        history[key]["down_since"]
                    )

                    down_hours = round(
                        (NOW - d).total_seconds() / 3600,
                        1
                    )

                samples = history[key]["samples"]

                up_count = sum(
                    1 for s in samples if s["up"]
                )

                service_24h = round(
                    (up_count / len(samples)) * 100,
                    1
                ) if samples else 0

                gateways.append({
                    "cluster": site["name"],
                    "name": name,
                    "status": status,
                    "connection": connection_label,
                    "gateway_id": gateway_id,
                    "model": model,
                    "firmware": firmware,
                    "city": city,
                    "geo": geo,
                    "down": down,
                    "down_since": history[key]["down_since"],
                    "down_hours": down_hours,
                    "service_24h": service_24h,
                    "maintenance": down_hours >= 24,
                    "last_connection": last_connection
                })

        except Exception as e:

            print(site["name"], str(e))

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
    (ok / total) * 100,
    1
) if total else 0


clusters = {}

for g in gateways:

    c = g["cluster"]

    if c not in clusters:

        clusters[c] = {
            "total": 0,
            "down": 0
        }

    clusters[c]["total"] += 1

    if g["down"]:
        clusters[c]["down"] += 1


html = f"""
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
    margin:0;
    font-family:-apple-system,BlinkMacSystemFont,sans-serif;
    background:#f5f5f7;
    color:#111;
}}

.wrapper {{
    max-width:1600px;
    margin:auto;
    padding:30px;
}}

h1 {{
    font-size:48px;
    font-weight:700;
}}

.cards {{
    display:grid;
    grid-template-columns:
    repeat(auto-fit,minmax(220px,1fr));
    gap:20px;
    margin:30px 0;
}}

.card {{
    background:rgba(255,255,255,0.6);
    backdrop-filter:blur(20px);
    border-radius:28px;
    padding:25px;
    box-shadow:
    0 10px 30px rgba(0,0,0,0.08);
}}

.big {{
    font-size:42px;
    font-weight:700;
    margin-top:10px;
}}

.green {{
    color:#16a34a;
}}

.red {{
    color:#dc2626;
}}

.orange {{
    color:#ea580c;
}}

.cluster-grid {{
    display:grid;
    grid-template-columns:
    repeat(auto-fit,minmax(250px,1fr));
    gap:20px;
}}

.cluster-card {{
    background:white;
    border-radius:24px;
    padding:20px;
    box-shadow:
    0 10px 30px rgba(0,0,0,0.06);
}}

.table-wrap {{
    overflow:auto;
    margin-top:20px;
    background:white;
    border-radius:24px;
    box-shadow:
    0 10px 30px rgba(0,0,0,0.06);
}}

table {{
    width:100%;
    border-collapse:collapse;
}}

th {{
    background:#f1f5f9;
    padding:16px;
    text-align:left;
}}

td {{
    padding:16px;
    border-top:1px solid #e2e8f0;
}}

.badge {{
    padding:8px 14px;
    border-radius:30px;
    color:white;
    font-weight:600;
}}

.ok {{
    background:#16a34a;
}}

.ko {{
    background:#dc2626;
}}

.down {{
    background:#fff1f2;
}}

</style>

</head>

<body>

<div class="wrapper">

<h1>Monitoring Requea LoRaWAN</h1>

<p>
Dernière mise à jour :
{NOW.astimezone().strftime("%d/%m/%Y %H:%M")}
</p>

<div class="cards">

<div class="card">
Clusters
<div class="big">{len(clusters)}</div>
</div>

<div class="card">
Passerelles
<div class="big">{total}</div>
</div>

<div class="card">
Connectées
<div class="big green">{ok}</div>
</div>

<div class="card">
Déconnectées
<div class="big red">{down}</div>
</div>

<div class="card">
Service
<div class="big green">{service}%</div>
</div>

</div>

<h2>Synthèse clusters</h2>

<div class="cluster-grid">
"""

for name, data in clusters.items():

    html += f"""
<div class="cluster-card">
<h3>{esc(name)}</h3>
<p>Passerelles : {data["total"]}</p>
<p>Déconnectées : {data["down"]}</p>
</div>
"""

html += """
</div>

<h2>Passerelles à traiter</h2>

<div class="table-wrap">

<table>

<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>Géolocalisation</th>
<th>Connexion</th>
<th>Dernière connexion</th>
<th>Durée HS</th>
</tr>
"""

for g in gateways:

    if not g["down"]:
        continue

    html += f"""
<tr class="down">
<td>{esc(g["cluster"])}</td>
<td>{esc(g["name"])}</td>
<td>{esc(g["city"])}</td>
<td>{esc(g["geo"])}</td>
<td>
<span class="badge ko">
Déconnectée
</span>
</td>
<td>{esc(g["last_connection"])}</td>
<td>{esc(g["down_hours"])} h</td>
</tr>
"""

html += """
</table>
</div>

<h2>Toutes les passerelles</h2>

<div class="table-wrap">

<table>

<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>Géolocalisation</th>
<th>Connexion</th>
<th>Firmware</th>
</tr>
"""

for g in gateways:

    badge = "ko" if g["down"] else "ok"

    html += f"""
<tr>
<td>{esc(g["cluster"])}</td>
<td>{esc(g["name"])}</td>
<td>{esc(g["city"])}</td>
<td>{esc(g["geo"])}</td>
<td>
<span class="badge {badge}">
{esc(g["connection"])}
</span>
</td>
<td>{esc(g["firmware"])}</td>
</tr>
"""

html += """
</table>
</div>

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

    f.write(html)

print(f"Dashboard généré : {total} passerelles")
