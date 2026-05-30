from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os, json, html, re, statistics

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)

VALENCE_URL = "https://lora.valenceromansagglo.fr"
TEST_GATEWAY_ID = "00000008004AB09A"

SUSPECT_GATEWAYS = {
    "00000008004E608F",
    "00000008004AB09A",
    "00000008004DB072",
}

REFERENCE_GATEWAYS = {
    "00000008004E6311",
}

traffic_rows = []


def esc(v):
    return html.escape(str(v or ""))


def clean(v):
    return " ".join(str(v or "").replace("\n", " ").replace("\t", " ").replace("\xa0", " ").split()).strip()


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
    text = clean(text)
    dates = find_dates(text)

    # On compte les dates visibles comme approximation du nombre de messages.
    # Sur la page Requea "Voir les messages", chaque ligne de message commence par une date.
    messages = []

    for dt in dates:
        messages.append({
            "time": dt,
        })

    return messages


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


def login(page, cluster):
    page.goto(cluster["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    body = clean(page.locator("body").inner_text())

    # Si la session est déjà ouverte, inutile de chercher le formulaire.
    if (
        "Sign out" in body
        or "Déconnexion" in body
        or "Deconnexion" in body
        or "Network Map" in body
        or "Gateways" in body
    ):
        return

    username_selectors = [
        'input[name*="login" i]:visible',
        'input[name*="user" i]:visible',
        'input[name*="email" i]:visible',
        'input[type="email"]:visible',
        'input[type="text"]:visible',
        'input:visible:not([type="password"]):not([type="hidden"])',
    ]

    password_selectors = [
        'input[type="password"]:visible',
        'input[name*="password" i]:visible',
        'input[name*="pass" i]:visible',
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
            "body_start": body[:2000],
            "input_count": page.locator("input").count(),
        }

        os.makedirs("public", exist_ok=True)

        with open("public/valence_traffic.html", "w", encoding="utf-8") as f:
            f.write(
                "<!doctype html><html><head><meta charset='utf-8'>"
                "<title>Erreur connexion Valence</title></head><body>"
                "<h1>Erreur connexion Valence</h1>"
                "<p>Formulaire de connexion introuvable.</p>"
                "<pre>"
                + esc(json.dumps(debug, indent=2, ensure_ascii=False))
                + "</pre>"
                "</body></html>"
            )

        raise Exception("Formulaire de connexion Valence introuvable. Voir public/valence_traffic.html")

    username.fill(cluster.get("login", ""))
    password.fill(cluster.get("password", ""))

    page.wait_for_timeout(300)
    password.press("Enter")
    page.wait_for_timeout(7000)

    body = page.locator("body").inner_text()

    if "Mot de passe oublié" in body or "Forgot your password" in body:
        raise Exception("Connexion refusée")


def click_next(page):
    clicked = page.evaluate("""
() => {
    const els = Array.from(document.querySelectorAll("a,button,span,div"));
    const visible = el => {
        const r = el.getBoundingClientRect();
        const s = window.getComputedStyle(el);
        return r.width > 0 && r.height > 0 && s.display !== "none" && s.visibility !== "hidden";
    };

    for (const el of els) {
        if (!visible(el)) continue;

        const txt = (el.innerText || el.textContent || "").trim().toLowerCase();
        const cls = (el.className || "").toString().toLowerCase();
        const title = (el.getAttribute("title") || "").toLowerCase();
        const aria = (el.getAttribute("aria-label") || "").toLowerCase();

        if (cls.includes("disabled")) continue;
        if (el.getAttribute("disabled") !== null) continue;

        if (
            txt === ">" ||
            txt === "›" ||
            txt === "suivant" ||
            txt === "next" ||
            cls.includes("next") ||
            title.includes("suivant") ||
            title.includes("next") ||
            aria.includes("suivant") ||
            aria.includes("next")
        ) {
            el.click();
            return true;
        }
    }

    return false;
}
""")

    if clicked:
        page.wait_for_timeout(2500)

    return clicked


def collect_gateway_ids(page):
    ids = set()

    page.goto(f"{VALENCE_URL}/page/Network_Gateways", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(6000)

    visited = set()

    for _ in range(20):
        body = page.locator("body").inner_text()

        for m in re.finditer(r"\b[0-9A-Fa-f]{12,32}\b", body):
            ids.add(m.group(0).upper())

        sig = "|".join(sorted(ids))

        if sig in visited:
            break

        visited.add(sig)

        if not click_next(page):
            break

    return sorted(ids)


def open_gateway(page, gateway_id):
    page.goto(f"{VALENCE_URL}/page/Network_Gateways", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(5000)

    for _ in range(20):
        try:
            target = page.get_by_text(gateway_id, exact=True).first
            target.click()
            page.wait_for_timeout(4000)

            if gateway_id in page.locator("body").inner_text():
                return page.url

        except Exception:
            pass

        if not click_next(page):
            break

    return ""


def open_messages(page):
    candidates = [
        "View messages",
        "Voir les messages",
        "Messages",
    ]

    for label in candidates:
        try:
            page.get_by_text(label, exact=True).first.click()
            page.wait_for_timeout(3500)
            return True
        except Exception:
            pass

    # Dernier recours : chercher un lien contenant viewMessages.
    try:
        href = page.locator('a[href*="viewMessages"]').first.get_attribute("href")
        if href:
            if href.startswith("http"):
                page.goto(href, wait_until="domcontentloaded", timeout=60000)
            elif href.startswith("/"):
                page.goto(VALENCE_URL.rstrip("/") + href, wait_until="domcontentloaded", timeout=60000)
            else:
                page.goto(VALENCE_URL.rstrip("/") + "/" + href.lstrip("/"), wait_until="domcontentloaded", timeout=60000)

            page.wait_for_timeout(3500)
            return True
    except Exception:
        pass

    return False


def read_gateway_messages(context, gateway_id):
    page = context.new_page()
    ajax_payloads = []

    def on_response(response):
        try:
            if "/ajax" in response.url:
                txt = response.text()
                if "iotDeviceMessage" in txt or re.search(r"[0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2}", txt):
                    ajax_payloads.append(txt)
        except Exception:
            pass

    page.on("response", on_response)

    detail_url = ""
    messages_opened = False

    try:
        detail_url = open_gateway(page, gateway_id)

        if detail_url:
            messages_opened = open_messages(page)

        page.wait_for_timeout(8000)

        body_text = page.locator("body").inner_text()
        all_text = body_text + "\n" + "\n".join(ajax_payloads)

        messages = extract_message_rows(all_text)
        stats = stats_from_messages(messages)

        page.close()

        return {
            "gateway_id": gateway_id,
            "detail_url": detail_url,
            "messages_opened": messages_opened,
            **stats,
        }

    except Exception as e:
        try:
            page.close()
        except Exception:
            pass

        return {
            "gateway_id": gateway_id,
            "detail_url": detail_url,
            "messages_opened": messages_opened,
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


# Premier fichier de test :
# - par défaut, on teste uniquement la passerelle 00000008004AB09A pour valider le mécanisme.
# - quand le test est OK, passer TEST_ONLY à False pour balayer tout Valence.
TEST_ONLY = True


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1600, "height": 1000})

    cluster = None

    for c in CONFIG:
        url = str(c.get("url", "")).rstrip("/").lower()
        name = str(c.get("name", "")).lower()

        if (
            url == VALENCE_URL.rstrip("/").lower()
            or "valenceromans" in url
            or "valence" in name
        ):
            cluster = c
            break

    if not cluster:
        raise Exception("Cluster Valence introuvable dans REQUEA_CONFIG")

    page = context.new_page()
    login(page, cluster)

    if TEST_ONLY:
        gateway_ids = [TEST_GATEWAY_ID]
    else:
        gateway_ids = collect_gateway_ids(page)

    page.close()

    for gid in gateway_ids:
        print("Analyse trafic", gid)
        traffic_rows.append(read_gateway_messages(context, gid))

    context.close()
    browser.close()


visible_counts = [r["messages_visible"] for r in traffic_rows if r.get("messages_visible", 0) > 0]
median_visible = statistics.median(visible_counts) if visible_counts else 0

traffic_rows = sorted(
    traffic_rows,
    key=lambda r: (
        r["gateway_id"] not in SUSPECT_GATEWAYS,
        r.get("messages_visible", 0)
    )
)


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
    --soft:#8b98a9;
    --line:rgba(255,255,255,.64);
    --shadow:0 22px 70px rgba(31,41,55,.14), inset 0 1px 0 rgba(255,255,255,.70);
    --shadow-soft:0 12px 34px rgba(31,41,55,.08), inset 0 1px 0 rgba(255,255,255,.72);
    --blue:#1473ff;
    --cyan:#00b8f5;
    --green:#16c784;
    --red:#ff3b5c;
    --orange:#ff9f0a;
    --violet:#7c3aed;
}}

* {{
    box-sizing:border-box;
}}

html {{
    -webkit-font-smoothing:antialiased;
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

.hero,
.panel {{
    position:relative;
    overflow:hidden;
    border:1px solid var(--line);
    background:linear-gradient(145deg,rgba(255,255,255,.46),rgba(255,255,255,.20));
    box-shadow:var(--shadow);
    backdrop-filter:blur(38px) saturate(210%);
    -webkit-backdrop-filter:blur(38px) saturate(210%);
}}

.hero::before,
.panel::before {{
    content:"";
    position:absolute;
    inset:0;
    pointer-events:none;
    background:linear-gradient(145deg,rgba(255,255,255,.60),rgba(255,255,255,0) 42%,rgba(255,255,255,.20));
}}

.hero > *,
.panel > * {{
    position:relative;
    z-index:2;
}}

.hero {{
    border-radius:38px;
    padding:30px;
    margin-bottom:22px;
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
    box-shadow:0 18px 42px rgba(20,115,255,.28), inset 0 1px 0 rgba(255,255,255,.35);
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
    color:#07101f;
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
    box-shadow:var(--shadow-soft);
}}

.kpis {{
    display:grid;
    grid-template-columns:repeat(4,minmax(0,1fr));
    gap:14px;
    margin-top:28px;
}}

.kpi {{
    position:relative;
    overflow:hidden;
    min-height:132px;
    padding:18px;
    border-radius:27px;
    color:white;
    box-shadow:0 18px 45px rgba(15,23,42,.14), inset 0 1px 0 rgba(255,255,255,.38);
}}

.kpi::before {{
    content:"";
    position:absolute;
    inset:0;
    background:linear-gradient(145deg,rgba(255,255,255,.36),rgba(255,255,255,.05));
}}

.kpi > * {{
    position:relative;
    z-index:2;
}}

.kpi-label {{
    font-size:13px;
    font-weight:850;
    opacity:.96;
}}

.kpi-value {{
    font-size:36px;
    line-height:1;
    font-weight:950;
    letter-spacing:-.05em;
    margin-top:8px;
}}

.kpi-sub {{
    margin-top:8px;
    font-size:12px;
    font-weight:700;
    opacity:.88;
}}

.g-blue {{ background:linear-gradient(145deg,#1473ff,#58b8ff); }}
.g-cyan {{ background:linear-gradient(145deg,#00b8f5,#67e8f9); }}
.g-red {{ background:linear-gradient(145deg,#f43f5e,#fb7185); }}
.g-violet {{ background:linear-gradient(145deg,#7c3aed,#a78bfa); }}

.panel {{
    border-radius:32px;
    padding:24px;
    margin-bottom:22px;
    box-shadow:var(--shadow-soft);
}}

.section-title {{
    margin:0;
    font-size:31px;
    line-height:1.05;
    font-weight:950;
    letter-spacing:-.055em;
    color:#07101f;
}}

.section-caption {{
    color:var(--muted);
    font-weight:650;
    font-size:14px;
    margin-top:7px;
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
    min-width:1100px;
    border-collapse:collapse;
}}

th {{
    position:sticky;
    top:0;
    z-index:3;
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

tr:hover {{
    background:rgba(255,255,255,.36);
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

@media(max-width:900px) {{
    body {{ padding:10px; }}
    .hero,.panel {{ border-radius:24px; padding:16px; }}
    h1 {{ font-size:31px; }}
    .section-title {{ font-size:25px; }}
    .kpis {{ grid-template-columns:repeat(2,1fr); gap:10px; }}
    .kpi {{ min-height:118px; padding:14px; border-radius:22px; }}
    .kpi-value {{ font-size:29px; }}
}}
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
                <div class="subtitle">Comparaison des trames collectées par passerelle.</div>
            </div>
        </div>
        <div class="updated">Mise à jour · {NOW.strftime("%d/%m/%Y %H:%M")}</div>
    </div>

    <div class="kpis">
        <div class="kpi g-blue">
            <div class="kpi-label">Passerelles analysées</div>
            <div class="kpi-value">{len(traffic_rows)}</div>
            <div class="kpi-sub">mode {"test" if TEST_ONLY else "cluster complet"}</div>
        </div>
        <div class="kpi g-cyan">
            <div class="kpi-label">Médiane visible</div>
            <div class="kpi-value">{median_visible}</div>
            <div class="kpi-sub">messages sur page courante</div>
        </div>
        <div class="kpi g-red">
            <div class="kpi-label">Suspectes suivies</div>
            <div class="kpi-value">{len(SUSPECT_GATEWAYS)}</div>
            <div class="kpi-sub">passerelles prioritaires</div>
        </div>
        <div class="kpi g-violet">
            <div class="kpi-label">Références</div>
            <div class="kpi-value">{len(REFERENCE_GATEWAYS)}</div>
            <div class="kpi-sub">comparaison proximité</div>
        </div>
    </div>
</section>

<section class="panel">
    <h2 class="section-title">Comparaison trafic</h2>
    <div class="section-caption">Première version : comptage des messages visibles sur la page "Voir les messages".</div>

    <div class="table-wrap">
        <table>
            <tr>
                <th>Passerelle</th>
                <th>Messages visibles</th>
                <th>Messages 1h</th>
                <th>Messages 24h</th>
                <th>Dernier message</th>
                <th>% médiane</th>
                <th>Statut</th>
                <th>Page messages ouverte</th>
            </tr>
"""

for row in traffic_rows:
    r = ratio(row.get("messages_visible", 0), median_visible)
    badge = "ko" if r < 30 else ("warn" if r < 60 else "ok")

    html_page += f"""
            <tr>
                <td><strong>{esc(row["gateway_id"])}</strong></td>
                <td>{row.get("messages_visible", 0)}</td>
                <td>{row.get("messages_1h", 0)}</td>
                <td>{row.get("messages_24h", 0)}</td>
                <td>{fmt_date(row.get("last_message"))}</td>
                <td>{r}%</td>
                <td><span class="badge {badge}">{esc(status_label(row, median_visible))}</span></td>
                <td>{'Oui' if row.get("messages_opened") else 'Non'}</td>
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

print(f"Dashboard trafic généré : {len(traffic_rows)} passerelles analysées")
