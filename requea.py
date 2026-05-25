from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import os, json, html, re

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

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
    return " ".join(
        str(v or "")
        .replace("\n", " ")
        .replace("\t", " ")
        .replace("\xa0", " ")
        .split()
    ).strip()


def strip_tags(v):
    return clean(
        re.sub(r"<[^>]+>", " ", html.unescape(str(v or "")))
    )


def fmt_date(v):
    if not v:
        return "-"

    try:
        return (
            datetime.fromisoformat(v)
            .astimezone(PARIS)
            .strftime("%d/%m/%Y %H:%M:%S")
        )
    except Exception:
        return str(v)


def normalize_connection(v):
    t = str(v or "").lower()

    if (
        "déconnect" in t
        or "deconnect" in t
        or "offline" in t
        or "closed" in t
    ):
        return "Déconnectée", True

    if (
        "connectée" in t
        or "connectee" in t
        or "connected" in t
    ):
        return "Connectée", False

    return clean(v), False


def parse_last_connection(text):
    text = clean(text)

    m = re.search(
        r"Derni[eè]re\s+connexion\s*:?\s*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if not m:
        return None

    try:
        return datetime.strptime(
            m.group(1),
            "%d/%m/%Y %H:%M:%S"
        ).replace(tzinfo=PARIS)
    except Exception:
        return None


def geoloc_from_text(text):
    m = re.search(
        r"([0-9]{2}\.[0-9]+)[,\s]+([0-9]{1,2}\.[0-9]+)",
        text
    )

    if not m:
        return ""

    return f"{m.group(1)}, {m.group(2)}"


def login(page, cluster):
    page.goto(
        cluster["url"],
        wait_until="domcontentloaded",
        timeout=60000
    )

    page.wait_for_timeout(4000)

    username = page.locator(
        'input:visible:not([type="hidden"]):not([type="password"])'
    ).first

    password = page.locator(
        'input[type="password"]:visible'
    ).first

    username.fill(cluster["login"])
    password.fill(cluster["password"])

    page.wait_for_timeout(500)

    password.press("Enter")

    page.wait_for_timeout(10000)

    body = page.locator("body").inner_text()

    if (
        "Mot de passe oublié" in body
        or "Forgot your password" in body
    ):
        raise Exception("ECHEC LOGIN")


def parse_gateway(values, raw, cluster_name, detail_url=""):
    values = [clean(v) for v in values if clean(v)]

    gateway_id = ""

    for v in values:
        if re.fullmatch(r"[0-9A-Fa-f]{12,32}", v):
            gateway_id = v
            break

    if not gateway_id:
        return None

    status = ""
    for v in values:
        if v.lower() == "active":
            status = v
            break

    if status.lower() != "active":
        return None

    connection_raw = ""

    for v in values:
        low = v.lower()

        if (
            "connect" in low
            or "offline" in low
            or "closed" in low
            or "déconnect" in low
        ):
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
        if (
            "multitech" in v.lower()
            or "kerlink" in v.lower()
        ):
            model = v
            break

    name = gateway_id

    try:
        idx = values.index("Active")

        if idx > 0:
            name = values[idx - 1]
    except Exception:
        pass

    city = ""

    if firmware and firmware in values:
        idx = values.index(firmware)

        if idx + 1 < len(values):
            city = values[idx + 1]

    return {
        "cluster": cluster_name,
        "name": name,
        "status": status,
        "gateway_id": gateway_id,
        "model": model,
        "connection": connection,
        "firmware": firmware,
        "city": city,
        "geolocation": geoloc_from_text(raw),
        "down": is_down,
        "detail_url": detail_url,
        "last_connection": None,
    }


def parse_ajax_html(html_text, cluster_name, base_url):
    found = {}

    rows = re.findall(
        r"<tr[^>]*>.*?</tr>",
        html_text,
        flags=re.I | re.S
    )

    for row_html in rows:
        raw = strip_tags(row_html)

        if (
            "iotGateway" not in row_html
            and not re.search(r"[0-9A-Fa-f]{12,32}", raw)
        ):
            continue

        detail_url = ""

        decoded = html.unescape(row_html)

        m = re.search(
            r"RQ\.nav\.detail\('([^']+)'",
            decoded,
            re.I
        )

        if m:
            path = html.unescape(m.group(1))

            if path.startswith("/"):
                detail_url = base_url.rstrip("/") + path
            else:
                detail_url = base_url.rstrip("/") + "/" + path

        cells = re.findall(
            r"<td[^>]*>(.*?)</td>",
            row_html,
            flags=re.I | re.S
        )

        values = [strip_tags(c) for c in cells]

        gateway = parse_gateway(
            values,
            raw,
            cluster_name,
            detail_url
        )

        if gateway:
            found[gateway["gateway_id"]] = gateway

    return found


def collect_visible_rows(page, cluster):
    found = {}

    rows = page.locator("tr")

    for i in range(rows.count()):
        row = rows.nth(i)

        raw = clean(row.inner_text())

        if not re.search(r"[0-9A-Fa-f]{12,32}", raw):
            continue

        cells = row.locator("td")

        values = [
            cells.nth(j).inner_text()
            for j in range(cells.count())
        ]

        detail_url = ""

        try:
            onclick = row.get_attribute("onclick") or ""

            m = re.search(
                r"RQ\.nav\.detail\('([^']+)'",
                onclick
            )

            if m:
                path = m.group(1)

                if path.startswith("/"):
                    detail_url = cluster["url"].rstrip("/") + path
                else:
                    detail_url = cluster["url"].rstrip("/") + "/" + path

        except Exception:
            pass

        gateway = parse_gateway(
            values,
            raw,
            cluster["name"],
            detail_url
        )

        if gateway:
            found[gateway["gateway_id"]] = gateway

    return found


def click_next(page):
    return page.evaluate("""
        () => {
            const els = Array.from(document.querySelectorAll("a,button,span"));

            for (const el of els) {
                const txt = (el.innerText || "").trim().toLowerCase();

                if (
                    txt === ">" ||
                    txt === "›" ||
                    txt.includes("suivant")
                ) {
                    el.click();
                    return true;
                }
            }

            return false;
        }
    """)


def read_last_connection(context, gateway):
    if not gateway["detail_url"]:
        return None

    try:
        p = context.new_page()

        p.goto(
            gateway["detail_url"],
            wait_until="domcontentloaded",
            timeout=60000
        )

        p.wait_for_timeout(5000)

        body = p.locator("body").inner_text()

        p.close()

        return parse_last_connection(body)

    except Exception:
        return None


def apply_history(g):
    key = g["gateway_id"]

    if key not in history:
        history[key] = {
            "down_since": None,
            "samples": []
        }

    if g["down"]:
        if g["last_connection"]:
            history[key]["down_since"] = g["last_connection"]
    else:
        history[key]["down_since"] = None

    history[key]["samples"].append({
        "time": NOW.isoformat(),
        "up": not g["down"]
    })

    history[key]["samples"] = [
        s for s in history[key]["samples"]
        if (
            NOW - datetime.fromisoformat(s["time"])
        ).total_seconds() <= 86400
    ]

    samples = history[key]["samples"]

    g["service_24h"] = (
        round(
            sum(1 for s in samples if s["up"])
            / len(samples)
            * 100,
            1
        )
        if samples else 0
    )

    g["down_since"] = history[key]["down_since"]

    g["down_hours"] = 0

    if g["down_since"]:
        start = datetime.fromisoformat(g["down_since"])

        g["down_hours"] = round(
            (NOW - start).total_seconds() / 3600,
            1
        )

    g["maintenance"] = g["down_hours"] >= 24

    return g


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)

    for cluster in CONFIG:
        context = browser.new_context()
        page = context.new_page()

        ajax_payloads = []

        def on_response(response):
            try:
                if "/ajax" in response.url:
                    txt = response.text()

                    if (
                        "iotGateway" in txt
                        or "mtcdt" in txt
                        or re.search(r"[0-9A-Fa-f]{12,32}", txt)
                    ):
                        ajax_payloads.append(txt)

            except Exception:
                pass

        page.on("response", on_response)

        try:
            login(page, cluster)

            page.goto(
                f'{cluster["url"]}/page/Network_Gateways',
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(12000)

            seen = {}

            for _ in range(20):
                current = collect_visible_rows(page, cluster)

                for k, v in current.items():
                    seen[k] = v

                for payload in ajax_payloads:
                    parsed = parse_ajax_html(
                        payload,
                        cluster["name"],
                        cluster["url"]
                    )

                    for k, v in parsed.items():
                        seen[k] = v

                moved = click_next(page)

                if not moved:
                    break

                page.wait_for_timeout(7000)

            final_gateways = []

            for gateway_id, gateway in seen.items():
                if gateway["down"]:
                    last = read_last_connection(
                        context,
                        gateway
                    )

                    if last:
                        gateway["last_connection"] = last.isoformat()

                final_gateways.append(
                    apply_history(gateway)
                )

            gateways.extend(final_gateways)

        except Exception as e:
            gateways.append({
                "cluster": cluster["name"],
                "name": "ERREUR",
                "status": "Erreur",
                "gateway_id": "",
                "model": "",
                "connection": str(e),
                "firmware": "",
                "city": "",
                "geolocation": "",
                "down": True,
                "detail_url": "",
                "last_connection": None,
                "down_since": None,
                "down_hours": 0,
                "service_24h": 0,
                "maintenance": False
            })

        context.close()

    browser.close()


with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(
        history,
        f,
        indent=2,
        ensure_ascii=False
    )


active_gateways = [
    g for g in gateways
    if g["status"] == "Active"
]

total = len(active_gateways)

down = len([
    g for g in active_gateways
    if g["down"]
])

ok = total - down

maintenance = len([
    g for g in active_gateways
    if g["maintenance"]
])

service = (
    round(ok / total * 100, 1)
    if total else 0
)

clusters = sorted(
    set(g["cluster"] for g in active_gateways)
)

cluster_stats = {}

for c in clusters:
    cg = [
        g for g in active_gateways
        if g["cluster"] == c
    ]

    c_total = len(cg)

    c_down = len([
        g for g in cg
        if g["down"]
    ])

    c_ok = c_total - c_down

    cluster_stats[c] = {
        "total": c_total,
        "ok": c_ok,
        "down": c_down,
        "service": round(
            c_ok / c_total * 100,
            1
        ) if c_total else 0
    }


html_page = f"""
<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<title>Monitoring Requea</title>

<style>

body {{
    background:#0f172a;
    color:white;
    font-family:Arial;
    margin:0;
    padding:20px;
}}

.cards {{
    display:grid;
    grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
    gap:16px;
    margin-bottom:24px;
}}

.card {{
    background:#1e293b;
    border-radius:16px;
    padding:20px;
}}

.big {{
    font-size:34px;
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
    min-width:1400px;
}}

th {{
    background:#334155;
    padding:12px;
    text-align:left;
}}

td {{
    padding:12px;
    border-bottom:1px solid #334155;
}}

.table-wrap {{
    overflow:auto;
    margin-bottom:40px;
}}

.badge {{
    padding:6px 10px;
    border-radius:999px;
}}

.ok {{
    background:#166534;
}}

.ko {{
    background:#991b1b;
}}

tr.down {{
    background:#451a1a;
}}

tr.maintenance {{
    background:#7f1d1d;
}}

button {{
    border:0;
    padding:10px 14px;
    border-radius:12px;
    margin-right:6px;
    margin-bottom:10px;
}}

</style>

<script>

function filterCluster(cluster) {{

    document.querySelectorAll(".gateway-row")
    .forEach(row => {{

        row.style.display =
            cluster === "ALL"
            || row.dataset.cluster === cluster
            ? ""
            : "none";
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

<div class="card">
Maintenance >24h
<div class="big orange">{maintenance}</div>
</div>

</div>

<h2>🌍 Synthèse clusters</h2>

<div class="cards">
"""

for c in clusters:
    s = cluster_stats[c]

    html_page += f"""
    <div class="card">
    <strong>{esc(c)}</strong>

    <div>Passerelles : {s["total"]}</div>
    <div>Connectées : {s["ok"]}</div>
    <div>Déconnectées : {s["down"]}</div>
    <div>Service : {s["service"]}%</div>

    </div>
    """

html_page += """
</div>

<h2>🌍 Filtre cluster</h2>

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
<h2>🚨 Passerelles HS</h2>

<div class="table-wrap">

<table>

<tr>
<th>Cluster</th>
<th>Passerelle</th>
<th>Ville</th>
<th>Connexion</th>
<th>Dernière connexion</th>
<th>HS depuis</th>
<th>Durée HS</th>
<th>Service 24h</th>
<th>Firmware</th>
</tr>
"""

for g in active_gateways:

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
        data-cluster="{esc(g["cluster"])}"
    >

    <td>{esc(g["cluster"])}</td>
    <td>{esc(g["name"])}</td>
    <td>{esc(g["city"])}</td>

    <td>
    <span class="badge ko">
    {esc(g["connection"])}
    </span>
    </td>

    <td>{fmt_date(g["last_connection"])}</td>
    <td>{fmt_date(g["down_since"])}</td>
    <td>{g["down_hours"]} h</td>
    <td>{g["service_24h"]}%</td>
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
<th>Statut</th>
<th>Connexion</th>
<th>Dernière connexion</th>
<th>Firmware</th>
<th>ID</th>
</tr>
"""

for g in active_gateways:

    badge = "ko" if g["down"] else "ok"

    row_class = (
        "maintenance"
        if g["maintenance"]
        else (
            "down"
            if g["down"]
            else ""
        )
    )

    html_page += f"""
    <tr
        class="gateway-row {row_class}"
        data-cluster="{esc(g["cluster"])}"
    >

    <td>{esc(g["cluster"])}</td>
    <td>{esc(g["name"])}</td>
    <td>{esc(g["city"])}</td>
    <td>{esc(g["status"])}</td>

    <td>
    <span class="badge {badge}">
    {esc(g["connection"])}
    </span>
    </td>

    <td>{fmt_date(g["last_connection"])}</td>

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
    f"Dashboard généré : "
    f"{total} passerelles actives"
)
