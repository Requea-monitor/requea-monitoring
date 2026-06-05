from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import os, json, html, re

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

EXTRA_CLUSTERS = [
    {"name": "SIEA", "url": "https://siea.requea.com"},
    {"name": "CCVCMB", "url": "https://ccvcmb.requea.com"},
    {"name": "Valence Romans", "url": "https://lora.valenceromansagglo.fr"},
]

if CONFIG:
    default_login = CONFIG[0].get("login", "")
    default_password = CONFIG[0].get("password", "")
    existing = {c["url"].rstrip("/") for c in CONFIG}

    for extra in EXTRA_CLUSTERS:
        if extra["url"].rstrip("/") not in existing:
            CONFIG.append({
                "name": extra["name"],
                "url": extra["url"],
                "login": default_login,
                "password": default_password
            })

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
    return html.escape(str(v or ""))


def clean(v):
    return " ".join(str(v or "").replace("\n", " ").replace("\t", " ").replace("\xa0", " ").split()).strip()


def strip_tags(v):
    v = html.unescape(str(v or ""))
    v = re.sub(r"<script.*?</script>", " ", v, flags=re.I | re.S)
    v = re.sub(r"<style.*?</style>", " ", v, flags=re.I | re.S)
    v = re.sub(r"<[^>]+>", " ", v)
    return clean(v)


def fmt_date(v):
    if not v:
        return "-"
    try:
        return datetime.fromisoformat(v).astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(v)


def parse_requea_date(text):
    text = html.unescape(str(text or ""))
    text = clean(text)

    m = re.search(
        r"([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        text,
        re.I
    )

    if m:
        try:
            return datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
        except Exception:
            pass

    m = re.search(
        r"([0-9]{1,2}/[0-9]{1,2}/[0-9]{4},\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s+[AP]M)",
        text,
        re.I
    )

    if m:
        try:
            return datetime.strptime(m.group(1), "%m/%d/%Y, %I:%M:%S %p").replace(tzinfo=PARIS)
        except Exception:
            pass

    return None


def parse_last_connection_from_html(html_text):
    decoded = html.unescape(str(html_text or ""))
    text = strip_tags(decoded)

    patterns = [
        r"Derni[eè]re\s+connexion[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Derniere\s+connexion[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Last\s+connection[^0-9]*([0-9]{1,2}/[0-9]{1,2}/[0-9]{4},\s+[0-9]{1,2}:[0-9]{2}:[0-9]{2}\s+[AP]M)",
    ]

    for source in [decoded, text]:
        for pattern in patterns:
            m = re.search(pattern, source, re.I | re.S)
            if m:
                return parse_requea_date(m.group(1))

    return None


def normalize_connection(v):
    t = str(v or "").lower()

    if "déconnect" in t or "deconnect" in t or "closed" in t or "offline" in t or "down" in t:
        return "Déconnectée", True

    if "connectée" in t or "connectee" in t or "connected" in t or "online" in t:
        return "Connectée", False

    return clean(v) or "Inconnue", True


def geoloc_from_text(text):
    text = clean(text)

    patterns = [
        r"([0-9]{2}\.[0-9]+)\s*,\s*([0-9]{1,2}\.[0-9]+)",
        r"([0-9]{2}\.[0-9]+)\s+([0-9]{1,2}\.[0-9]+)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I)
        if m:
            return normalize_geolocation(f"{m.group(1)}, {m.group(2)}")

    return ""


def normalize_geolocation(value):
    """Retourne une seule paire GPS propre, même si le texte contient les coordonnées plusieurs fois."""
    text = clean(value)
    m = re.search(r"([0-9]{2}\.[0-9]+)\s*,\s*([0-9]{1,2}\.[0-9]+)", text)
    if not m:
        return ""
    return f"{m.group(1)}, {m.group(2)}"


def gps_display(value):
    return normalize_geolocation(value) or "-"


def parse_label_value(text, labels):
    text = clean(text)
    labels = [re.escape(label) for label in labels]

    if not labels:
        return ""

    label_pattern = "|".join(labels)

    patterns = [
        rf"(?:{label_pattern})\s*:\s*(.+?)(?=\s+[A-ZÀ-Ÿ][A-Za-zÀ-ÿ0-9 /_-]{{2,40}}\s*:|$)",
        rf"(?:{label_pattern})\s+(.+?)(?=\s+[A-ZÀ-Ÿ][A-Za-zÀ-ÿ0-9 /_-]{{2,40}}\s*:|$)",
    ]

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
        if m:
            value = clean(m.group(1))
            value = re.sub(
                r"\s+(Informations|Network|Connectivity|Supervision|Radio Links|Modbus/Bacnet|Spectral Analysis|Remote Shell|Alarms)\b.*$",
                "",
                value,
                flags=re.I
            )
            return value[:160]

    return ""


def enrich_gateway_from_detail_text(gateway, text):
    if not text:
        return

    gps = geoloc_from_text(text)
    if gps:
        gateway["geolocation"] = gps

    sim = parse_label_value(text, [
        "SIM", "Carte SIM", "SIM card", "ICCID", "N° SIM", "Numero SIM", "Numéro SIM"
    ])

    imei = parse_label_value(text, [
        "IMEI", "Modem IMEI", "Identifiant IMEI"
    ])

    commentaire = parse_label_value(text, [
        "Commentaire", "Commentaires", "Comment", "Comments", "Description"
    ])

    connection_serveur = parse_label_value(text, [
        "Connection serveur", "Connexion serveur", "Server connection", "Server status",
        "Etat serveur", "État serveur", "Etat de connexion serveur"
    ])

    alimentation = parse_label_value(text, [
        "Alimentation", "Power", "Power supply", "Supply", "Batterie", "Battery",
        "Tension", "Voltage"
    ])

    if sim and not gateway.get("sim"):
        gateway["sim"] = sim

    if imei and not gateway.get("imei"):
        gateway["imei"] = imei

    if commentaire and not gateway.get("commentaire"):
        gateway["commentaire"] = commentaire

    if connection_serveur and not gateway.get("connection_serveur"):
        gateway["connection_serveur"] = connection_serveur

    if alimentation and not gateway.get("alimentation"):
        gateway["alimentation"] = alimentation


def gps_pair(geolocation):
    m = re.search(r"([0-9]{2}\.[0-9]+)\s*,\s*([0-9]{1,2}\.[0-9]+)", str(geolocation or ""))
    if not m:
        return None, None
    return m.group(1), m.group(2)


def maps_url(geolocation):
    lat, lon = gps_pair(geolocation)
    if not lat or not lon:
        return ""
    return f"https://www.google.com/maps/search/?api=1&query={lat},{lon}"


def waze_url(geolocation):
    lat, lon = gps_pair(geolocation)
    if not lat or not lon:
        return ""
    return f"https://waze.com/ul?ll={lat},{lon}&navigate=yes"


def icon_link(url, label, svg):
    if not url:
        return '<span class="icon-link disabled" title="Coordonnées absentes">' + svg + '</span>'
    return f'<a class="icon-link" href="{esc(url)}" target="_blank" rel="noopener" title="{esc(label)}">{svg}</a>'


def gps_actions(geolocation):
    map_svg = '<svg viewBox="0 0 24 24"><path d="M9 18 3 21V6l6-3 6 3 6-3v15l-6 3-6-3Z"/><path d="M9 3v15M15 6v15"/></svg>'
    waze_svg = '<svg viewBox="0 0 24 24"><path d="M5 14a7 7 0 1 1 2 5l-3 1 1-3a7 7 0 0 1 0-3Z"/><circle cx="9" cy="12" r=".6"/><circle cx="15" cy="12" r=".6"/><path d="M9 16c1.8 1 4.2 1 6 0"/></svg>'
    return (
        '<span class="gps-actions">'
        + icon_link(maps_url(geolocation), "Ouvrir dans Maps", map_svg)
        + icon_link(waze_url(geolocation), "Ouvrir dans Waze", waze_svg)
        + '</span>'
    )


def gateway_link(gateway):
    url = gateway.get("detail_url") or ""
    svg = '<svg viewBox="0 0 24 24"><path d="M14 3h7v7"/><path d="M21 3 10 14"/><path d="M12 5H5a2 2 0 0 0-2 2v12a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-7"/></svg>'
    if not url:
        return '<span class="icon-link disabled" title="Lien Requea absent">' + svg + '</span>'
    return f'<a class="icon-link" href="{esc(url)}" target="_blank" rel="noopener" title="Ouvrir la passerelle dans Requea">{svg}</a>'


def is_valence_cluster(cluster):
    url = str(cluster.get("url", "")).lower()
    name = str(cluster.get("name", "")).lower()
    return "valenceromansagglo" in url or "valence" in name


def gateways_list_url(cluster):
    base = cluster["url"].rstrip("/")

    if is_valence_cluster(cluster):
        return f"{base}/do/NetworkMap/Home/iotGateway:list"

    return f"{base}/page/Network_Gateways"


def make_absolute_url(base_url, url):
    if not url:
        return ""

    url = html.unescape(url).replace("&amp;", "&")

    if url.startswith("http"):
        return url

    if url.startswith("/"):
        return base_url.rstrip("/") + url

    return base_url.rstrip("/") + "/" + url.lstrip("/")


def login(page, cluster):
    page.goto(cluster["url"], wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(2500)

    username = page.locator(
        'input:visible:not([type="password"]):not([type="hidden"])'
    ).first

    password = page.locator(
        'input[type="password"]:visible'
    ).first

    username.fill(cluster["login"])
    password.fill(cluster["password"])

    page.wait_for_timeout(500)
    password.press("Enter")
    page.wait_for_timeout(6000)

    body = page.locator("body").inner_text()

    if "Mot de passe oublié" in body or "Forgot your password" in body:
        raise Exception("Connexion refusée")


def extract_detail_url_from_html(row_html, base_url):
    decoded = html.unescape(row_html).replace("&amp;", "&")

    patterns = [
        r"(/do/(?:NetworkMap/|Network/|NetworkMap/Home/|Network/Home/)?iotGateway:get\?[^'\"<>\s]+)",
        r"(/do/[^'\"<>\s]*iotGateway:get\?[^'\"<>\s]+)",
        r"RQ\.nav\.detail\('([^']*iotGateway:get[^']*)'",
        r"RQ\.nav\.go\('([^']*iotGateway:get[^']*)'",
        r'href="([^"]*iotGateway:get[^"]*)"',
        r"href='([^']*iotGateway:get[^']*)'",
    ]

    for pattern in patterns:
        m = re.search(pattern, decoded, re.I)
        if m:
            return make_absolute_url(base_url, m.group(1))

    return ""


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
            status = "Active"
            break

    if status != "Active":
        return None

    connection_raw = ""

    for v in values:
        low = v.lower()
        if "connect" in low or "closed" in low or "offline" in low or "déconnect" in low or "deconnect" in low:
            connection_raw = v
            break

    connection, is_down = normalize_connection(connection_raw)

    firmware = ""

    for v in values:
        low = v.lower()
        if "mtcdt" in low or "mtcap" in low or "firmware" in low:
            firmware = v
            break

    sim_from_listing = ""

    for v in values:
        candidate = clean(v).replace(" ", "")
        if candidate != gateway_id and re.fullmatch(r"[0-9]{18,24}", candidate):
            sim_from_listing = candidate
            break

    model = ""

    for v in values:
        if "multitech" in v.lower() or "kerlink" in v.lower():
            model = v
            break

    name = gateway_id

    if "Active" in values:
        idx = values.index("Active")
        if idx > 0:
            name = values[idx - 1]

    geolocation = geoloc_from_text(raw)

    if not geolocation:
        for v in values:
            geolocation = geoloc_from_text(v)
            if geolocation:
                break

    geolocation = normalize_geolocation(geolocation)

    # Filtre anti-obsolètes / provisionnement :
    # une passerelle réellement exploitable sur la carte réseau doit avoir des coordonnées GPS.
    # Les entrées Active sans GPS sont ignorées pour éviter les doublons/remplacements/anciennes fiches.
    if not geolocation:
        return None

    city = ""

    if firmware and firmware in values:
        idx = values.index(firmware)
        if idx + 1 < len(values):
            city = values[idx + 1]

    if not city:
        for v in reversed(values):
            if (
                v
                and v not in [gateway_id, name, status, model, firmware, connection_raw, geolocation]
                and not geoloc_from_text(v)
                and len(v) < 80
            ):
                city = v
                break

    return {
        "cluster": cluster_name,
        "name": name,
        "status": "Active",
        "gateway_id": gateway_id,
        "model": model,
        "connection": connection,
        "firmware": firmware,
        "city": city,
        "geolocation": geolocation,
        "down": is_down,
        "detail_url": detail_url,
        "last_connection": None,
        "connected_since": None,
        "sim": sim_from_listing,
        "imei": "",
        "commentaire": "",
        "connection_serveur": "",
        "alimentation": "",
    }


def parse_ajax_html(html_text, cluster_name, base_url):
    found = {}

    rows = re.findall(r"<tr[^>]*>.*?</tr>", html_text, flags=re.I | re.S)

    for row_html in rows:
        raw = strip_tags(row_html)

        if not re.search(r"[0-9A-Fa-f]{12,32}", raw):
            continue

        cells = re.findall(r"<td[^>]*>(.*?)</td>", row_html, flags=re.I | re.S)
        values = [strip_tags(c) for c in cells]

        detail_url = extract_detail_url_from_html(row_html, base_url)

        gateway = parse_gateway(values, raw, cluster_name, detail_url)

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
        values = [cells.nth(j).inner_text() for j in range(cells.count())]

        detail_url = ""

        try:
            links = row.locator("a")
            for link_index in range(links.count()):
                href = links.nth(link_index).get_attribute("href") or ""
                if "iotGateway:get" in href:
                    detail_url = make_absolute_url(cluster["url"], href)
                    break
        except Exception:
            pass

        try:
            if not detail_url:
                row_html = row.evaluate("el => el.outerHTML")
                detail_url = extract_detail_url_from_html(row_html, cluster["url"])
        except Exception:
            pass

        try:
            if not detail_url:
                onclick = row.get_attribute("onclick") or ""
                match = re.search(r"RQ\.nav\.(?:detail|go)\('([^']*iotGateway:get[^']*)'", onclick)
                if match:
                    detail_url = make_absolute_url(cluster["url"], match.group(1))
        except Exception:
            pass

        gateway = parse_gateway(values, raw, cluster["name"], detail_url)

        if gateway:
            found[gateway["gateway_id"]] = gateway

    return found


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


def read_connection_date(context, cluster, gateway):
    detail_url = gateway.get("detail_url") or ""

    # Ouverture directe de la fiche détail si l'URL est fournie par le listing.
    # Si l'URL manque, on ne relance pas une recherche lente par pagination.
    if not detail_url:
        return None

    p = context.new_page()

    try:
        p.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
        p.wait_for_timeout(2500)

        gateway["detail_url"] = p.url

        body_text = p.locator("body").inner_text()
        html_detail = p.content()

        last = parse_last_connection_from_html(body_text)

        if not last:
            last = parse_last_connection_from_html(html_detail)

        enrich_gateway_from_detail_text(gateway, body_text)
        enrich_gateway_from_detail_text(gateway, html_detail)

        detail_tabs = [
            "Connectivité", "Connectivity",
            "Réseau", "Network",
            "Supervision",
            "Accès distant", "Remote Shell",
            "Modbus/Bacnet"
        ]

        for tab in detail_tabs:
            try:
                p.get_by_text(tab, exact=True).first.click()
                p.wait_for_timeout(700)
                tab_text = p.locator("body").inner_text()
                enrich_gateway_from_detail_text(gateway, tab_text)
            except Exception:
                pass

        p.close()
        return last

    except Exception:
        try:
            p.close()
        except Exception:
            pass

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
    else:
        history[key]["down_since"] = None

    history[key]["samples"].append({
        "time": NOW.isoformat(),
        "up": not g["down"]
    })

    history[key]["samples"] = [
        s for s in history[key]["samples"]
        if (NOW - datetime.fromisoformat(s["time"])).total_seconds() <= 86400
    ]

    samples = history[key]["samples"]

    g["service_24h"] = (
        round(sum(1 for s in samples if s["up"]) / len(samples) * 100, 1)
        if samples else 0
    )

    g["down_since"] = history[key]["down_since"]
    g["down_hours"] = 0

    if g["down_since"]:
        start = datetime.fromisoformat(g["down_since"])
        g["down_hours"] = round((NOW - start).total_seconds() / 3600, 1)

    g["maintenance"] = g["down_hours"] >= 24

    # Cache des champs de fiche détail : on évite de rouvrir les fiches inutilement.
    cached_fields = [
        "last_connection",
        "detail_url",
        "sim",
        "imei",
        "commentaire",
        "connection_serveur",
        "alimentation",
    ]

    for field in cached_fields:
        if g.get(field):
            history[key][field] = g[field]
        elif history[key].get(field):
            g[field] = history[key][field]


    if not g["down"]:
        g["connected_since"] = g["last_connection"]

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
                gateways_list_url(cluster),
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(5000)

            seen = {}
            visited = set()

            for _ in range(20):
                for k, v in collect_visible_rows(page, cluster).items():
                    seen[k] = v

                for payload in ajax_payloads:
                    for k, v in parse_ajax_html(payload, cluster["name"], cluster["url"]).items():
                        seen[k] = v

                sig = "|".join(sorted(seen.keys()))

                if sig in visited:
                    break

                visited.add(sig)

                if not click_next(page):
                    break

            for gateway_id, gateway in seen.items():
                # Optimisation : la fiche détail est lente. On l'ouvre uniquement pour les passerelles HS,
                # car c'est là que la dernière connexion et les champs diagnostic sont réellement utiles.
                if gateway["down"]:
                    connection_date = read_connection_date(context, cluster, gateway)

                    if connection_date:
                        gateway["last_connection"] = connection_date.isoformat()

                gateways.append(apply_history(gateway))

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
                "connected_since": None,
                "down_since": None,
                "down_hours": 0,
                "service_24h": 0,
                "maintenance": False
            })

        context.close()

    browser.close()


with open(HISTORY_FILE, "w", encoding="utf-8") as f:
    json.dump(history, f, indent=2, ensure_ascii=False)


active_gateways = [
    g for g in gateways
    if g["status"] == "Active" and normalize_geolocation(g.get("geolocation"))
]

total = len(active_gateways)
down = len([g for g in active_gateways if g["down"]])
ok = total - down
maintenance = len([g for g in active_gateways if g["maintenance"]])
service = round(ok / total * 100, 1) if total else 0

clusters = sorted(set(g["cluster"] for g in active_gateways))

cluster_stats = {}

for c in clusters:
    cg = [g for g in active_gateways if g["cluster"] == c]

    c_total = len(cg)
    c_down = len([g for g in cg if g["down"]])
    c_ok = c_total - c_down

    cluster_stats[c] = {
        "total": c_total,
        "ok": c_ok,
        "down": c_down,
        "service": round(c_ok / c_total * 100, 1) if c_total else 0
    }


def fmt_duration(hours):
    try:
        h = float(hours)
    except Exception:
        return "-"

    if h <= 0:
        return "0 min"

    if h < 1:
        return f"{round(h * 60)} min"

    if h < 24:
        return f"{h:.1f} h"

    days = int(h // 24)
    remaining_hours = int(h % 24)

    if days == 1:
        return f"1 jour {remaining_hours} h"

    return f"{days} jours {remaining_hours} h"



def support_icon(g):
    text = " ".join([
        str(g.get("name", "")),
        str(g.get("city", "")),
        str(g.get("model", "")),
        str(g.get("firmware", "")),
        str(g.get("commentaire", "")),
    ]).lower()

    if "église" in text or "eglise" in text or "church" in text:
        svg = '<svg viewBox="0 0 24 24"><path d="M12 3v18"/><path d="M8 7h8"/><path d="M5 21h14"/><path d="M7 21V10l5-4 5 4v11"/></svg>'
    elif "mairie" in text or "hôtel de ville" in text or "hotel de ville" in text:
        svg = '<svg viewBox="0 0 24 24"><path d="M4 21h16"/><path d="M6 21V10h12v11"/><path d="M5 10l7-5 7 5"/><path d="M9 21v-6h6v6"/></svg>'
    elif "poteau" in text or "ep " in text or "eclairage" in text or "éclairage" in text:
        svg = '<svg viewBox="0 0 24 24"><path d="M12 21V8"/><path d="M8 8h8"/><path d="M15 8a4 4 0 0 0-4-4H9"/><path d="M16 12h2"/></svg>'
    elif "hlm" in text or "immeuble" in text or "bâtiment" in text or "batiment" in text:
        svg = '<svg viewBox="0 0 24 24"><path d="M5 21V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16"/><path d="M9 7h1M14 7h1M9 11h1M14 11h1M9 15h1M14 15h1"/><path d="M3 21h18"/></svg>'
    elif "réservoir" in text or "reservoir" in text or "chateau" in text or "château" in text or "eau" in text:
        svg = '<svg viewBox="0 0 24 24"><path d="M7 8h10"/><path d="M8 8c0-3 8-3 8 0v4c0 3-8 3-8 0V8Z"/><path d="M9 15 6 21M15 15l3 6M9 18h6"/></svg>'
    else:
        svg = '<svg viewBox="0 0 24 24"><path d="M6 20h12M12 20V10"/><path d="M8 10a4 4 0 0 1 8 0"/><path d="M5 7a8 8 0 0 1 14 0"/></svg>'

    return '<span class="gateway-icon">' + svg + '</span>'


html_page = f"""
<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Monitoring Requea</title>
<style>
:root {{
    --ink:#0b1220;
    --muted:#667085;
    --line:rgba(255,255,255,.58);
    --glass:rgba(255,255,255,.34);
    --glass2:rgba(255,255,255,.52);
    --shadow:0 18px 60px rgba(31,41,55,.13), inset 0 1px 0 rgba(255,255,255,.65);
    --blue:#1677ff;
    --cyan:#05bdf2;
    --green:#19c37d;
    --red:#ff3b5c;
    --orange:#ff9f0a;
    --violet:#7c3aed;
}}
*{{box-sizing:border-box}}
html{{-webkit-font-smoothing:antialiased;}}
body{{
    margin:0;
    padding:22px;
    font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif;
    color:var(--ink);
    background:
        radial-gradient(circle at 12% 8%, rgba(22,119,255,.26), transparent 26%),
        radial-gradient(circle at 84% 6%, rgba(124,58,237,.22), transparent 28%),
        radial-gradient(circle at 52% 95%, rgba(5,189,242,.20), transparent 32%),
        linear-gradient(180deg,#ffffff 0%,#f7faff 44%,#edf4ff 100%);
}}
body::before{{
    content:"";
    position:fixed;
    inset:0;
    pointer-events:none;
    background-image:linear-gradient(rgba(255,255,255,.32) 1px, transparent 1px),linear-gradient(90deg,rgba(255,255,255,.26) 1px, transparent 1px);
    background-size:44px 44px;
    mask-image:linear-gradient(to bottom,rgba(0,0,0,.35),transparent 70%);
}}
.shell{{max-width:1680px;margin:0 auto;}}
.hero,.panel{{
    position:relative;
    overflow:hidden;
    background:linear-gradient(145deg,rgba(255,255,255,.50),rgba(255,255,255,.24));
    border:1px solid var(--line);
    box-shadow:var(--shadow);
    backdrop-filter:blur(34px) saturate(190%);
    -webkit-backdrop-filter:blur(34px) saturate(190%);
}}
.hero::after,.panel::after{{
    content:"";
    position:absolute;
    inset:1px;
    border-radius:inherit;
    pointer-events:none;
    background:linear-gradient(145deg,rgba(255,255,255,.58),rgba(255,255,255,0) 38%,rgba(255,255,255,.18));
}}
.hero>* ,.panel>*{{position:relative;z-index:2}}
.hero{{border-radius:36px;padding:32px;margin-bottom:22px;}}
.topbar{{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap;}}
.brand{{display:flex;align-items:center;gap:16px;}}
.logo{{
    width:64px;height:64px;border-radius:22px;
    display:flex;align-items:center;justify-content:center;
    color:white;font-weight:900;font-size:22px;letter-spacing:-.04em;
    background:linear-gradient(145deg,#1677ff,#7c3aed);
    box-shadow:0 18px 38px rgba(22,119,255,.30),inset 0 1px 0 rgba(255,255,255,.38);
}}
h1{{margin:0;font-size:44px;line-height:.96;font-weight:900;letter-spacing:-.055em;}}
.eyebrow{{font-size:13px;font-weight:800;letter-spacing:.08em;text-transform:uppercase;color:#2563eb;margin-bottom:8px;}}
.subtitle{{color:var(--muted);font-size:15px;margin-top:10px;font-weight:550;}}
.updated{{
    padding:12px 17px;border-radius:999px;
    background:rgba(255,255,255,.46);border:1px solid rgba(255,255,255,.68);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.72),0 12px 30px rgba(15,23,42,.07);
    color:#344054;font-size:14px;font-weight:750;
}}
.kpis{{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:14px;margin-top:28px;}}
.kpi{{
    position:relative;overflow:hidden;min-height:134px;padding:18px;border-radius:26px;color:white;
    box-shadow:0 18px 44px rgba(15,23,42,.13), inset 0 1px 0 rgba(255,255,255,.35);
}}
.kpi::before{{content:"";position:absolute;inset:0;background:linear-gradient(145deg,rgba(255,255,255,.34),rgba(255,255,255,.06));}}
.kpi::after{{content:"";position:absolute;right:-34px;top:-34px;width:116px;height:116px;border-radius:999px;background:rgba(255,255,255,.20);}}
.kpi>*{{position:relative;z-index:2}}
.kpi-icon{{width:42px;height:42px;border-radius:15px;background:rgba(255,255,255,.23);display:grid;place-items:center;margin-bottom:15px;}}
.kpi-icon svg{{width:22px;height:22px;stroke:white;stroke-width:2.2;fill:none;stroke-linecap:round;stroke-linejoin:round;}}
.kpi-label{{font-size:13px;font-weight:800;opacity:.95;}}
.kpi-value{{font-size:34px;font-weight:900;letter-spacing:-.045em;margin-top:5px;}}
.g-blue{{background:linear-gradient(145deg,#1677ff,#55b6ff)}}
.g-cyan{{background:linear-gradient(145deg,#06b6d4,#67e8f9)}}
.g-green{{background:linear-gradient(145deg,#12b76a,#4ade80)}}
.g-red{{background:linear-gradient(145deg,#f43f5e,#fb7185)}}
.g-orange{{background:linear-gradient(145deg,#f79009,#facc15)}}
.g-violet{{background:linear-gradient(145deg,#7c3aed,#a78bfa)}}
.panel{{border-radius:32px;padding:24px;margin-bottom:22px;}}
.section-head{{display:flex;align-items:flex-end;justify-content:space-between;gap:16px;margin-bottom:18px;}}
h2{{margin:0;font-size:30px;line-height:1.05;font-weight:900;letter-spacing:-.045em;}}
.section-caption{{color:var(--muted);font-weight:600;font-size:14px;margin-top:6px;}}
.cluster-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px;}}
.cluster-card{{
    position:relative;overflow:hidden;border-radius:24px;padding:18px;
    background:linear-gradient(145deg,rgba(255,255,255,.48),rgba(255,255,255,.22));
    border:1px solid rgba(255,255,255,.64);
    backdrop-filter:blur(26px) saturate(180%);
    -webkit-backdrop-filter:blur(26px) saturate(180%);
    box-shadow:0 12px 34px rgba(15,23,42,.08),inset 0 1px 0 rgba(255,255,255,.7);
}}
.cluster-card::after{{content:"";position:absolute;right:-24px;top:-24px;width:92px;height:92px;border-radius:999px;background:linear-gradient(145deg,rgba(22,119,255,.18),rgba(124,58,237,.16));}}
.cluster-top{{display:flex;align-items:center;gap:12px;position:relative;z-index:2;}}
.cluster-mark{{
    width:44px;height:44px;border-radius:16px;display:grid;place-items:center;color:white;
    background:linear-gradient(145deg,#1677ff,#7c3aed);box-shadow:0 12px 28px rgba(22,119,255,.22);
}}
.cluster-mark svg{{width:22px;height:22px;stroke:white;fill:none;stroke-width:2.25;stroke-linecap:round;stroke-linejoin:round;}}
.cluster-name{{font-size:18px;font-weight:900;letter-spacing:-.03em;}}
.cluster-stats{{display:grid;grid-template-columns:repeat(3,1fr);gap:8px;margin-top:16px;position:relative;z-index:2;}}
.cluster-num{{font-size:24px;font-weight:900;letter-spacing:-.04em;}}
.cluster-sub{{font-size:11px;color:var(--muted);font-weight:750;text-transform:uppercase;letter-spacing:.04em;}}
.progress{{position:relative;height:11px;background:rgba(226,232,240,.82);border-radius:999px;overflow:hidden;margin-top:16px;box-shadow:inset 0 1px 2px rgba(15,23,42,.08);}}
.progress span{{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,#1677ff,#05bdf2 55%,#19c37d);box-shadow:0 0 18px rgba(5,189,242,.35);}}
.progress-label{{font-size:12px;font-weight:850;color:#344054;text-align:right;margin-top:7px;}}
.filter-shell{{overflow-x:auto;padding-bottom:2px;-webkit-overflow-scrolling:touch;}}
.filter{{
    position:relative;display:inline-flex;gap:5px;min-width:max-content;padding:7px;border-radius:999px;
    background:linear-gradient(145deg,rgba(255,255,255,.38),rgba(255,255,255,.18));
    border:1px solid rgba(255,255,255,.72);
    box-shadow:
        inset 0 1px 0 rgba(255,255,255,.82),
        inset 0 -8px 18px rgba(255,255,255,.18),
        0 16px 38px rgba(15,23,42,.08);
    backdrop-filter:blur(26px) saturate(190%);
    -webkit-backdrop-filter:blur(26px) saturate(190%);
}}
.slider{{
    position:absolute;
    top:7px;
    left:7px;
    height:calc(100% - 14px);
    border-radius:999px;
    overflow:hidden;
    isolation:isolate;
    background:
        radial-gradient(circle at 28% 18%, rgba(255,255,255,.96), rgba(255,255,255,.58) 20%, rgba(255,255,255,.25) 58%, rgba(255,255,255,.10) 100%);
    border:1px solid rgba(255,255,255,.86);
    box-shadow:
        0 16px 38px rgba(15,23,42,.13),
        inset 0 1px 1px rgba(255,255,255,.98),
        inset 0 -16px 24px rgba(180,210,255,.22),
        inset 0 0 0 1px rgba(255,255,255,.42);
    backdrop-filter:blur(30px) saturate(220%) contrast(112%);
    -webkit-backdrop-filter:blur(30px) saturate(220%) contrast(112%);
    transition:
        transform .34s cubic-bezier(.2,.8,.2,1),
        width .34s cubic-bezier(.2,.8,.2,1);
}}
.slider::before{{
    content:"";
    position:absolute;
    inset:2px 10px auto 10px;
    height:45%;
    border-radius:999px;
    background:linear-gradient(180deg,rgba(255,255,255,.94),rgba(255,255,255,.18));
    pointer-events:none;
    mix-blend-mode:screen;
}}
.slider::after{{
    content:"";
    position:absolute;
    right:9px;
    bottom:7px;
    width:18px;
    height:18px;
    border-radius:999px;
    background:rgba(255,255,255,.38);
    box-shadow:0 0 18px rgba(255,255,255,.45);
    pointer-events:none;
}}
.seg-btn{{position:relative;z-index:2;border:0;background:transparent;border-radius:999px;padding:11px 17px;color:#334155;font-weight:850;white-space:nowrap;cursor:pointer;transition:color .22s ease,text-shadow .22s ease;}}
.seg-btn.active{{color:#07101f;text-shadow:0 1px 0 rgba(255,255,255,.65);}}
.table-wrap{{overflow:auto;border-radius:24px;border:1px solid rgba(255,255,255,.62);background:rgba(255,255,255,.28);}}
table{{width:100%;min-width:1500px;border-collapse:collapse;}}
th{{position:sticky;top:0;z-index:3;background:rgba(255,255,255,.72);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);color:#475467;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.035em;padding:14px;}}
td{{padding:14px;border-bottom:1px solid rgba(148,163,184,.15);white-space:nowrap;font-size:13px;}}
tr:hover{{background:rgba(255,255,255,.34);}}
.badge{{display:inline-flex;align-items:center;border-radius:999px;padding:7px 11px;font-size:12px;font-weight:850;}}
.ok{{background:#dcfae6;color:#067647}}
.ko{{background:#fee4e2;color:#b42318}}
.down{{background:rgba(254,226,226,.28)}}
.maintenance{{background:rgba(255,237,213,.35)}}
@media(max-width:1180px){{.kpis{{grid-template-columns:repeat(3,1fr)}}}}
@media(max-width:760px){{body{{padding:10px}}.hero,.panel{{border-radius:24px;padding:16px}}.logo{{width:54px;height:54px;border-radius:18px}}h1{{font-size:30px}}h2{{font-size:24px}}.kpis{{grid-template-columns:repeat(2,1fr);gap:10px}}.kpi{{min-height:118px;padding:14px;border-radius:21px}}.kpi-icon{{width:34px;height:34px;border-radius:12px;margin-bottom:11px}}.kpi-icon svg{{width:18px;height:18px}}.kpi-label{{font-size:12px}}.kpi-value{{font-size:28px}}.cluster-grid{{grid-template-columns:repeat(2,minmax(0,1fr));gap:10px}}.cluster-card{{padding:13px;border-radius:20px}}.cluster-mark{{width:36px;height:36px;border-radius:13px}}.cluster-name{{font-size:15px}}.cluster-num{{font-size:20px}}.cluster-sub{{font-size:10px}}.progress{{height:9px}}.seg-btn{{padding:10px 14px;font-size:13px}}}}

.kpi-sub{{margin-top:8px;font-size:12px;font-weight:700;opacity:.88;}}
.filter-row{{display:flex;gap:14px;align-items:center;flex-wrap:wrap;margin-top:18px;}}
.search{{min-width:270px;flex:1;}}
.search input{{width:100%;border:1px solid rgba(255,255,255,.68);background:rgba(255,255,255,.52);border-radius:999px;padding:13px 17px;outline:none;font-weight:750;color:#344054;box-shadow:0 12px 34px rgba(31,41,55,.08);backdrop-filter:blur(24px);-webkit-backdrop-filter:blur(24px);}}
@media(max-width:760px){{.search{{min-width:100%;}}.kpi-sub{{font-size:11px}}}}


.icon-link{{display:inline-grid;place-items:center;width:30px;height:30px;border-radius:999px;margin-left:6px;background:rgba(255,255,255,.48);border:1px solid rgba(255,255,255,.72);box-shadow:0 8px 20px rgba(15,23,42,.08),inset 0 1px 0 rgba(255,255,255,.78);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);color:#2563eb;text-decoration:none;vertical-align:middle;transition:transform .18s ease,box-shadow .18s ease,background .18s ease;}}
.icon-link:hover{{transform:translateY(-1px);background:rgba(255,255,255,.72);box-shadow:0 12px 26px rgba(15,23,42,.12),inset 0 1px 0 rgba(255,255,255,.86);}}
.icon-link svg{{width:16px;height:16px;stroke:currentColor;stroke-width:2.1;fill:none;stroke-linecap:round;stroke-linejoin:round;}}
.icon-link.disabled{{opacity:.28;cursor:not-allowed;color:#98a2b3;}}
.gps-cell,.gateway-cell{{display:inline-flex;align-items:center;gap:6px;}}
.gps-actions{{display:inline-flex;align-items:center;white-space:nowrap;}}


/* Export, Liquid Glass interactions, responsive tables */
.glass-export{{
    display:inline-flex;align-items:center;gap:10px;border:1px solid rgba(255,255,255,.76);border-radius:999px;padding:12px 16px;
    color:#07101f;font-weight:900;
    background:radial-gradient(circle at 28% 18%, rgba(255,255,255,.92), rgba(255,255,255,.40) 45%, rgba(255,255,255,.18) 100%);
    box-shadow:0 14px 34px rgba(15,23,42,.10), inset 0 1px 0 rgba(255,255,255,.90), inset 0 -12px 22px rgba(200,220,255,.18);
    backdrop-filter:blur(28px) saturate(205%);-webkit-backdrop-filter:blur(28px) saturate(205%);
    cursor:pointer;transition:transform .22s ease, box-shadow .22s ease, background .22s ease;
}}
.glass-export:hover{{transform:translateY(-1px) scale(1.012);box-shadow:0 18px 42px rgba(15,23,42,.14), inset 0 1px 0 rgba(255,255,255,.95), inset 0 -14px 24px rgba(200,220,255,.22);}}
.glass-export svg{{width:17px;height:17px;stroke:currentColor;fill:none;stroke-width:2.2;stroke-linecap:round;stroke-linejoin:round;}}
.panel{{animation:fadeUp .46s ease both;}}
.panel:nth-of-type(2){{animation-delay:.04s}}
.panel:nth-of-type(3){{animation-delay:.08s}}
.panel:nth-of-type(4){{animation-delay:.12s}}
@keyframes fadeUp{{from{{opacity:0;transform:translateY(12px) scale(.995)}}to{{opacity:1;transform:none}}}}
.kpi,.cluster-card{{transition:transform .24s ease, box-shadow .24s ease, filter .24s ease;}}
.kpi:hover,.cluster-card:hover{{transform:translateY(-2px);filter:saturate(1.04);}}
.seg-btn{{transition:transform .20s ease,color .20s ease,text-shadow .20s ease;}}
.seg-btn:hover{{transform:scale(1.025);}}
.gateway-row{{transition:background .18s ease, transform .18s ease;}}
.gateway-row:hover{{transform:translateX(2px);}}
.gateway-icon{{
    display:inline-grid;place-items:center;width:26px;height:26px;border-radius:999px;margin-right:8px;vertical-align:middle;color:#2563eb;
    background:rgba(255,255,255,.46);border:1px solid rgba(255,255,255,.74);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.85),0 8px 18px rgba(15,23,42,.07);
    backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);
}}
.gateway-icon svg{{width:15px;height:15px;stroke:currentColor;fill:none;stroke-width:2.15;stroke-linecap:round;stroke-linejoin:round;}}
td,th{{font-variant-numeric:tabular-nums;}}
td{{overflow-wrap:anywhere;}}
@media(max-width:760px){{
    table{{min-width:1180px}}
    th{{font-size:10px;padding:10px 8px;letter-spacing:.02em}}
    td{{font-size:11px;padding:10px 8px;line-height:1.25}}
    .badge{{font-size:10px;padding:6px 9px}}
    .icon-link{{width:27px;height:27px}}
    .gateway-icon{{width:23px;height:23px;margin-right:6px}}
    .glass-export{{width:100%;justify-content:center;margin-top:12px}}
}}
@media(prefers-reduced-motion:reduce){{*,*::before,*::after{{animation:none!important;transition:none!important;scroll-behavior:auto!important}}}}

</style>
<script>
function initSegmented() {{
    const buttons = Array.from(document.querySelectorAll(".seg-btn"));
    const slider = document.querySelector(".slider");
    const search = document.querySelector("#searchInput");

    function move(btn) {{
        if (!slider || !btn) return;
        slider.style.width = btn.offsetWidth + "px";
        slider.style.transform = `translateX(${{btn.offsetLeft}}px)`;
    }}

    function apply() {{
        const active = document.querySelector(".seg-btn.active");
        const cluster = active ? active.dataset.cluster : "ALL";
        const q = search ? search.value.toLowerCase().trim() : "";

        document.querySelectorAll(".gateway-row").forEach(row => {{
            const clusterOk = cluster === "ALL" || row.dataset.cluster === cluster;
            const searchOk = !q || row.innerText.toLowerCase().includes(q);
            row.style.display = clusterOk && searchOk ? "" : "none";
        }});

        move(active);
    }}

    buttons.forEach(btn => {{
        btn.addEventListener("click", () => {{
            buttons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            apply();
        }});
    }});

    if (search) search.addEventListener("input", apply);
    window.addEventListener("resize", apply);
    apply();
}}
window.addEventListener("load", initSegmented);

function initExportGateways(){{
    const btn = document.querySelector("#exportGateways");
    if (!btn) return;

    btn.addEventListener("click", () => {{
        const section = Array.from(document.querySelectorAll("section.panel")).find(s => {{
            const h = s.querySelector("h2");
            return h && h.textContent.trim().toLowerCase().includes("toutes les passerelles");
        }});
        if (!section) return;

        const table = section.querySelector("table");
        if (!table) return;

        const rows = Array.from(table.querySelectorAll("tr")).filter(row => row.style.display !== "none");
        const csv = rows.map(row => {{
            return Array.from(row.querySelectorAll("th,td")).map(cell => {{
                let value = cell.innerText.replace(/\s+/g, " ").trim();
                value = value.replace(/"/g, '""');
                return `"${{value}}"`;
            }}).join(";");
        }}).join("\\n");

        const blob = new Blob(["\\ufeff" + csv], {{type:"text/csv;charset=utf-8"}});
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        const date = new Date().toISOString().slice(0,10);
        a.href = url;
        a.download = "passerelles_requea_affichees_" + date + ".csv";
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
    }});
}}
window.addEventListener("load", initExportGateways);

</script>
</head>
<body>
<div class="shell">
<section class="hero">
    <div class="topbar">
        <div class="brand">
            <div class="logo">RQ</div>
            <div>
                <div class="eyebrow">Supervision LoRaWAN</div>
                <h1>Monitoring Requea</h1>
                <div class="subtitle">Vue opérationnelle des clusters et passerelles actives.</div>
            </div>
        </div>
        <div class="updated">Mise à jour · {NOW.strftime("%d/%m/%Y %H:%M")}</div>
    </div>
    <div class="kpis">
        <div class="kpi g-blue">
            <div class="kpi-icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg></div>
            <div class="kpi-label">Clusters</div>
            <div class="kpi-value">{len(clusters)}</div>
            <div class="kpi-sub">territoires supervisés</div>
        </div>
        <div class="kpi g-cyan">
            <div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M6 20h12M12 20V10"/><path d="M8 10a4 4 0 0 1 8 0M5 7a8 8 0 0 1 14 0"/></svg></div>
            <div class="kpi-label">Passerelles actives</div>
            <div class="kpi-value">{total}</div>
            <div class="kpi-sub">inventaire consolidé</div>
        </div>
        <div class="kpi g-green">
            <div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg></div>
            <div class="kpi-label">Connectées</div>
            <div class="kpi-value">{ok}</div>
            <div class="kpi-sub">{round(ok / total * 100, 1) if total else 0}% opérationnelles</div>
        </div>
        <div class="kpi g-red">
            <div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M12 8v5M12 17h.01"/><path d="M10.3 4.3 2.8 17.2A2 2 0 0 0 4.5 20h15a2 2 0 0 0 1.7-2.8L13.7 4.3a2 2 0 0 0-3.4 0Z"/></svg></div>
            <div class="kpi-label">Déconnectées</div>
            <div class="kpi-value">{down}</div>
            <div class="kpi-sub">{round(down / total * 100, 1) if total else 0}% à traiter</div>
        </div>
        <div class="kpi g-orange">
            <div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M4 19V5M4 19h16"/><path d="m7 15 4-4 3 3 5-7"/></svg></div>
            <div class="kpi-label">Service instantané</div>
            <div class="kpi-value">{service}%</div>
            <div class="kpi-sub">sur passerelles actives</div>
        </div>
        <div class="kpi g-violet">
            <div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5 5L4 17v3h3l5.7-5.7a4 4 0 0 0 5-5l-3 3-3-3 3-3Z"/></svg></div>
            <div class="kpi-label">Maintenance &gt;24h</div>
            <div class="kpi-value">{maintenance}</div>
            <div class="kpi-sub">priorité intervention</div>
        </div>
    </div>
</section>
<section class="panel">
    <div class="section-head"><div><h2>Synthèse clusters</h2><div class="section-caption">Identification rapide, état de service et incidents par territoire.</div></div></div>
    <div class="cluster-grid">
"""

for c in clusters:
    s = cluster_stats[c]
    html_page += f"""
        <article class="cluster-card">
            <div class="cluster-top"><div class="cluster-mark"><svg viewBox="0 0 24 24"><path d="M6 20h12M12 20V10"/><path d="M8 10a4 4 0 0 1 8 0M5 7a8 8 0 0 1 14 0"/></svg></div><div class="cluster-name">{esc(c)}</div></div>
            <div class="cluster-stats">
                <div><div class="cluster-num">{s["total"]}</div><div class="cluster-sub">Total</div></div>
                <div><div class="cluster-num" style="color:var(--green)">{s["ok"]}</div><div class="cluster-sub">OK</div></div>
                <div><div class="cluster-num" style="color:var(--red)">{s["down"]}</div><div class="cluster-sub">HS</div></div>
            </div>
            <div class="progress"><span style="width:{s["service"]}%"></span></div>
            <div class="progress-label">{s["service"]}%</div>
        </article>
"""

html_page += """
    </div>
</section>
<section class="panel">
    <div class="section-head"><div><h2>Filtrer les clusters</h2><div class="section-caption">Affichage dynamique du réseau.</div></div></div>
    <div class="filter-row"><div class="filter-shell"><div class="filter"><div class="slider"></div><button class="seg-btn active" data-cluster="ALL">Tous</button>
"""

for c in clusters:
    html_page += f'<button class="seg-btn" data-cluster="{esc(c)}">{esc(c)}</button>\n'

html_page += """
    </div></div><div class="search"><input id="searchInput" placeholder="Rechercher une passerelle, ville, firmware, ID..."></div></div>
</section>
<section class="panel">
    <div class="section-head"><div><h2>Passerelles HS</h2><div class="section-caption">Priorisation maintenance et durée d’indisponibilité.</div></div></div>
    <div class="table-wrap"><table>
        <tr><th>Cluster</th><th>Passerelle</th><th>Ville</th><th>GPS</th><th>Connexion</th><th>Dernière connexion</th><th>Durée HS</th><th>Service 24h</th><th>Firmware</th><th>SIM</th><th>IMEI</th><th>Commentaire</th><th>Connection serveur</th><th>Alimentation</th></tr>
"""

for g in active_gateways:
    if not g["down"]:
        continue
    row_class = "maintenance" if g["maintenance"] else "down"
    html_page += f"""
        <tr class="gateway-row {row_class}" data-cluster="{esc(g["cluster"])}">
            <td><strong>{esc(g["cluster"])}</strong></td><td><span class="gateway-cell">{support_icon(g)}<strong>{esc(g["name"])}</strong>{gateway_link(g)}</span></td><td>{esc(g["city"])}</td><td><span class="gps-cell">{esc(gps_display(g["geolocation"]))}{gps_actions(g["geolocation"])}</span></td>
            <td><span class="badge ko">{esc(g["connection"])}</span></td><td>{fmt_date(g["last_connection"])}</td><td>{fmt_duration(g["down_hours"])}</td><td>{g["service_24h"]}%</td><td>{esc(g["firmware"])}</td><td>{esc(g.get("sim"))}</td><td>{esc(g.get("imei"))}</td><td>{esc(g.get("commentaire"))}</td><td>{esc(g.get("connection_serveur"))}</td><td>{esc(g.get("alimentation"))}</td>
        </tr>
"""

html_page += """
    </table></div>
</section>
<section class="panel">
    <div class="section-head">
        <div><h2>Toutes les passerelles</h2><div class="section-caption">Inventaire consolidé des passerelles actives.</div></div>
        <button id="exportGateways" class="glass-export" type="button" title="Exporter les passerelles affichées">
            <svg viewBox="0 0 24 24"><path d="M12 3v12"/><path d="m7 10 5 5 5-5"/><path d="M5 21h14"/></svg>
            Export affiché
        </button>
    </div>
    <div class="table-wrap"><table>
        <tr><th>Cluster</th><th>Passerelle</th><th>Ville</th><th>GPS</th><th>Statut</th><th>Connexion</th><th>Firmware</th><th>ID</th></tr>
"""

for g in active_gateways:
    badge = "ko" if g["down"] else "ok"
    row_class = "maintenance" if g["maintenance"] else ("down" if g["down"] else "")
    html_page += f"""
        <tr class="gateway-row {row_class}" data-cluster="{esc(g["cluster"])}">
            <td><strong>{esc(g["cluster"])}</strong></td><td><span class="gateway-cell">{support_icon(g)}<strong>{esc(g["name"])}</strong>{gateway_link(g)}</span></td><td>{esc(g["city"])}</td><td><span class="gps-cell">{esc(gps_display(g["geolocation"]))}{gps_actions(g["geolocation"])}</span></td>
            <td><span class="badge ok">{esc(g["status"])}</span></td><td><span class="badge {badge}">{esc(g["connection"])}</span></td><td>{esc(g["firmware"])}</td><td>{esc(g["gateway_id"])}</td>
        </tr>
"""

html_page += """
    </table></div>
</section>
</div>
</body>
</html>
"""

os.makedirs("public", exist_ok=True)
with open("public/index.html", "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Dashboard généré : {total} passerelles actives")
