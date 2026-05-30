from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os, json, html, re, statistics

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)

VALENCE_URL = "https://lora.valenceromansagglo.fr"

# Test direct avec l'URL fournie.
# On commence volontairement par UNE passerelle pour valider l'accès "Voir messages".
TEST_GATEWAY_ID = "00000008004AB09A"
TEST_SYSID = "171716a74e724fe3a6d7ae8e0d252ed5"
TEST_PCTX = "9463c682c5eb466680da087220fa8d1a"

SUSPECT_GATEWAYS = {
    "00000008004E608F",
    "00000008004AB09A",
    "00000008004DB072",
}

REFERENCE_GATEWAYS = {
    "00000008004E6311",
}

TEST_ONLY = True
traffic_rows = []


def esc(v):
    return html.escape(str(v or ""))


def clean(v):
    return " ".join(str(v or "").replace("\n", " ").replace("\t", " ").replace("\xa0", " ").split()).strip()


def absolute_url(path):
    if not path:
        return ""
    path = html.unescape(str(path)).replace("&amp;", "&")
    if path.startswith("http"):
        return path
    if path.startswith("/"):
        return VALENCE_URL.rstrip("/") + path
    return VALENCE_URL.rstrip("/") + "/" + path.lstrip("/")


def parse_fr_datetime(v):
    v = clean(v)
    try:
        return datetime.strptime(v, "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
    except Exception:
        return None


def find_dates(text):
    found = []
    for m in re.finditer(
        r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        str(text or "")
    ):
        dt = parse_fr_datetime(m.group(1))
        if dt:
            found.append(dt)
    return found


def extract_message_rows(text):
    # Chaque ligne de message commence par une date.
    return [{"time": dt} for dt in find_dates(text)]


def stats_from_messages(messages):
    if not messages:
        return {
            "messages_visible": 0,
            "messages_1h": 0,
            "messages_24h": 0,
            "last_message": None,
        }

    one_hour = NOW - timedelta(hours=1)
    one_day = NOW - timedelta(hours=24)

    return {
        "messages_visible": len(messages),
        "messages_1h": sum(1 for m in messages if m["time"] >= one_hour),
        "messages_24h": sum(1 for m in messages if m["time"] >= one_day),
        "last_message": max(m["time"] for m in messages),
    }


def find_valence_credentials():
    if not CONFIG:
        raise Exception("REQUEA_CONFIG vide")

    # On privilégie une entrée Valence si elle existe.
    for c in CONFIG:
        url = str(c.get("url", "")).rstrip("/").lower()
        name = str(c.get("name", "")).lower()
        if (
            url == VALENCE_URL.rstrip("/").lower()
            or "valenceromans" in url
            or "valence" in name
        ):
            return {
                "name": c.get("name", "Valence Romans"),
                "url": VALENCE_URL,
                "login": c.get("login", ""),
                "password": c.get("password", "")
            }

    # Sinon on reprend les identifiants du premier cluster.
    c = CONFIG[0]
    return {
        "name": "Valence Romans",
        "url": VALENCE_URL,
        "login": c.get("login", ""),
        "password": c.get("password", "")
    }


def try_login_if_needed(page, cluster):
    body = clean(page.locator("body").inner_text())

    if (
        "Sign out" in body
        or "Déconnexion" in body
        or "Deconnexion" in body
        or "View messages" in body
        or "Voir les messages" in body
        or "iotDeviceMessage" in body
        or TEST_GATEWAY_ID in body
    ):
        return

    username_selectors = [
        'input[name*="login" i]:visible',
        'input[name*="user" i]:visible',
        'input[name*="email" i]:visible',
        'input[id*="login" i]:visible',
        'input[id*="user" i]:visible',
        'input[id*="email" i]:visible',
        'input[type="email"]:visible',
        'input[type="text"]:visible',
        'input:not([type]):visible',
        'input:visible:not([type="password"]):not([type="hidden"])',
    ]

    password_selectors = [
        'input[type="password"]:visible',
        'input[name*="password" i]:visible',
        'input[name*="pass" i]:visible',
        'input[id*="password" i]:visible',
        'input[id*="pass" i]:visible',
    ]

    username = None
    password = None

    for selector in username_selectors:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                username = loc.first
                break
        except Exception:
            pass

    for selector in password_selectors:
        try:
            loc = page.locator(selector)
            if loc.count() > 0:
                password = loc.first
                break
        except Exception:
            pass

    if not username or not password:
        debug = {
            "url": page.url,
            "title": page.title(),
            "body_start": body[:2500],
            "input_count": page.locator("input").count(),
        }
        print("DIAGNOSTIC CONNEXION VALENCE")
        print(json.dumps(debug, indent=2, ensure_ascii=False))
        raise Exception("Formulaire de connexion introuvable sur la page courante")

    username.fill(cluster.get("login", ""))
    password.fill(cluster.get("password", ""))
    page.wait_for_timeout(300)
    password.press("Enter")
    page.wait_for_timeout(7000)

    body = page.locator("body").inner_text()
    if "Mot de passe oublié" in body or "Forgot your password" in body:
        raise Exception("Connexion refusée")


def read_messages_direct(context, cluster, gateway_id, sysid, pctx):
    page = context.new_page()
    ajax_payloads = []

    def on_response(response):
        try:
            if "/ajax" in response.url:
                txt = response.text()
                if (
                    "iotDeviceMessage" in txt
                    or re.search(r"[0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2}", txt)
                ):
                    ajax_payloads.append(txt)
        except Exception:
            pass

    page.on("response", on_response)

    messages_url = (
        f"{VALENCE_URL}/do/NetworkMap/iotGateway:viewMessages"
        f"?sysId={sysid}&pctx={pctx}"
    )

    try:
        print("Ouverture directe messages:", messages_url)

        page.goto(messages_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        try_login_if_needed(page, cluster)

        # Après login, Requea peut rester sur la page login ou rediriger ailleurs.
        # On recharge l'URL messages pour être sûr d'être sur le bon écran.
        page.goto(messages_url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(8000)

        body_text = page.locator("body").inner_text()
        html_text = page.content()

        all_text = body_text + "\n" + html_text + "\n" + "\n".join(ajax_payloads)
        messages = extract_message_rows(all_text)
        stats = stats_from_messages(messages)

        debug = {
            "gateway_id": gateway_id,
            "url": page.url,
            "title": page.title(),
            "ajax_payloads": len(ajax_payloads),
            "messages_visible": stats["messages_visible"],
            "body_start": clean(body_text)[:1200],
        }

        print("RESULTAT TEST VALENCE")
        print(json.dumps(debug, indent=2, ensure_ascii=False))

        page.close()

        return {
            "gateway_id": gateway_id,
            "detail_url": messages_url,
            "messages_opened": True,
            **stats,
        }

    except Exception as e:
        try:
            body_text = page.locator("body").inner_text()
        except Exception:
            body_text = ""

        print("ERREUR TEST VALENCE")
        print(str(e))
        print(clean(body_text)[:2000])

        try:
            page.close()
        except Exception:
            pass

        return {
            "gateway_id": gateway_id,
            "detail_url": messages_url,
            "messages_opened": False,
            "messages_visible": 0,
            "messages_1h": 0,
            "messages_24h": 0,
            "last_message": None,
            "error": str(e),
        }


def fmt_date(v):
    if not v:
        return "-"
    try:
        return v.astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(v)


def ratio(value, median):
    if not median:
        return 0
    return round(value / median * 100, 1)


def status_label(row, median):
    r = ratio(row["messages_visible"], median)

    if row["gateway_id"] in SUSPECT_GATEWAYS:
        prefix = "Suspecte"
    elif row["gateway_id"] in REFERENCE_GATEWAYS:
        prefix = "Référence"
    else:
        prefix = "Standard"

    if r < 30:
        level = "Anomalie forte"
    elif r < 60:
        level = "Anomalie moyenne"
    else:
        level = "Normal"

    return f"{prefix} · {level}"


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1600, "height": 1000})

    cluster = find_valence_credentials()

    traffic_rows.append(
        read_messages_direct(
            context,
            cluster,
            TEST_GATEWAY_ID,
            TEST_SYSID,
            TEST_PCTX
        )
    )

    context.close()
    browser.close()


visible_counts = [
    r["messages_visible"]
    for r in traffic_rows
    if r.get("messages_visible", 0) > 0
]

median_visible = statistics.median(visible_counts) if visible_counts else 0

html_page = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Trafic Valence Romans · Requea</title>

<style>
:root {{
    --ink:#08111f;
    --muted:#5f6b7a;
    --line:rgba(255,255,255,.64);
    --shadow:0 22px 70px rgba(31,41,55,.14), inset 0 1px 0 rgba(255,255,255,.70);
    --shadow-soft:0 12px 34px rgba(31,41,55,.08), inset 0 1px 0 rgba(255,255,255,.72);
    --blue:#1473ff;
    --cyan:#00b8f5;
    --red:#ff3b5c;
    --violet:#7c3aed;
}}

* {{
    box-sizing:border-box;
}}

body {{
    margin:0;
    min-height:100vh;
    padding:22px;
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif;
    color:var(--ink);
    background:
        radial-gradient(circle at 8% 5%, rgba(20,115,255,.30), transparent 26%),
        radial-gradient(circle at 88% 8%, rgba(124,58,237,.24), transparent 28%),
        radial-gradient(circle at 56% 102%, rgba(0,184,245,.22), transparent 34%),
        linear-gradient(180deg,#ffffff 0%,#f7faff 46%,#edf5ff 100%);
}}

.shell {{
    max-width:1680px;
    margin:0 auto;
}}

.hero,.panel {{
    position:relative;
    overflow:hidden;
    border:1px solid var(--line);
    background:linear-gradient(145deg,rgba(255,255,255,.46),rgba(255,255,255,.20));
    box-shadow:var(--shadow);
    backdrop-filter:blur(38px) saturate(210%);
    -webkit-backdrop-filter:blur(38px) saturate(210%);
}}

.hero {{
    border-radius:38px;
    padding:30px;
    margin-bottom:22px;
}}

.panel {{
    border-radius:32px;
    padding:24px;
    margin-bottom:22px;
    box-shadow:var(--shadow-soft);
}}

.topbar {{
    display:flex;
    align-items:center;
    justify-content:space-between;
    gap:18px;
    flex-wrap:wrap;
}}

.brand {{
    display:flex;
    align-items:center;
    gap:16px;
}}

.logo {{
    width:66px;
    height:66px;
    border-radius:23px;
    display:grid;
    place-items:center;
    color:white;
    font-weight:900;
    font-size:20px;
    letter-spacing:-.05em;
    background:linear-gradient(145deg,#1473ff,#7c3aed);
}}

.eyebrow {{
    font-size:12px;
    font-weight:850;
    letter-spacing:.10em;
    text-transform:uppercase;
    color:#2563eb;
    margin-bottom:8px;
}}

h1 {{
    margin:0;
    font-size:46px;
    line-height:.95;
    font-weight:950;
    letter-spacing:-.06em;
}}

.subtitle {{
    color:var(--muted);
    font-size:15px;
    margin-top:10px;
    font-weight:600;
}}

.updated {{
    padding:12px 17px;
    border-radius:999px;
    background:rgba(255,255,255,.42);
    border:1px solid rgba(255,255,255,.72);
    color:#344054;
    font-size:14px;
    font-weight:800;
}}

.table-wrap {{
    overflow:auto;
    border-radius:24px;
    border:1px solid rgba(255,255,255,.62);
    background:rgba(255,255,255,.28);
    margin-top:18px;
}}

table {{
    width:100%;
    min-width:1000px;
    border-collapse:collapse;
}}

th {{
    position:sticky;
    top:0;
    background:rgba(255,255,255,.72);
    backdrop-filter:blur(20px);
    -webkit-backdrop-filter:blur(20px);
    color:#475467;
    text-align:left;
    font-size:12px;
    text-transform:uppercase;
    letter-spacing:.035em;
    padding:14px;
}}

td {{
    padding:14px;
    border-bottom:1px solid rgba(148,163,184,.15);
    white-space:nowrap;
    font-size:13px;
}}

.badge {{
    display:inline-flex;
    align-items:center;
    border-radius:999px;
    padding:7px 11px;
    font-size:12px;
    font-weight:850;
}}

.ok {{ background:#dcfae6; color:#067647; }}
.warn {{ background:#fef0c7; color:#b54708; }}
.ko {{ background:#fee4e2; color:#b42318; }}
</style>
</head>

<body>
<div class="shell">

<section class="hero">
    <div class="topbar">
        <div class="brand">
            <div class="logo">VR</div>
            <div>
                <div class="eyebrow">Analyse trafic LoRaWAN</div>
                <h1>Valence Romans</h1>
                <div class="subtitle">Test direct via l’URL Requea Voir messages fournie.</div>
            </div>
        </div>
        <div class="updated">Mise à jour · {NOW.strftime("%d/%m/%Y %H:%M")}</div>
    </div>
</section>

<section class="panel">
    <h2>Résultat test passerelle</h2>
    <div class="table-wrap">
        <table>
            <tr>
                <th>Passerelle</th>
                <th>Messages visibles</th>
                <th>Messages 1h</th>
                <th>Messages 24h</th>
                <th>Dernier message</th>
                <th>Statut</th>
                <th>Erreur</th>
            </tr>
"""

for row in traffic_rows:
    r = ratio(row.get("messages_visible", 0), median_visible)
    badge = "ko" if row.get("error") else ("warn" if row.get("messages_visible", 0) == 0 else "ok")

    html_page += f"""
            <tr>
                <td><strong>{esc(row["gateway_id"])}</strong></td>
                <td>{row.get("messages_visible", 0)}</td>
                <td>{row.get("messages_1h", 0)}</td>
                <td>{row.get("messages_24h", 0)}</td>
                <td>{fmt_date(row.get("last_message"))}</td>
                <td><span class="badge {badge}">{esc(status_label(row, median_visible))}</span></td>
                <td>{esc(row.get("error", ""))}</td>
            </tr>
"""

html_page += """
        </table>
    </div>
</section>

</div>
</body>
</html>
"""

os.makedirs("public", exist_ok=True)

with open("public/valence_traffic.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Dashboard trafic généré : {len(traffic_rows)} passerelle analysée")
