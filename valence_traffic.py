from playwright.sync_api import sync_playwright
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from bs4 import BeautifulSoup
import os, json, html, re, statistics, sys

CONFIG = json.loads(os.environ["REQUEA_CONFIG"])

PARIS = ZoneInfo("Europe/Paris")
NOW = datetime.now(PARIS)

VALENCE_URL = "https://lora.valenceromansagglo.fr"
OUTPUT_FILE = "public/valence_traffic.html"

# 6 pages = environ 90 trames max par passerelle.
# Pour accélérer : définir VALENCE_MAX_MESSAGE_PAGES=3 dans le workflow.
MAX_MESSAGE_PAGES_PER_GATEWAY = int(os.environ.get("VALENCE_MAX_MESSAGE_PAGES", "6"))

TARGET_GATEWAYS = {
    "00000008004E608F": "Suspecte · Gambetta bis",
    "00000008004AB09A": "Suspecte proche",
    "00000008004DB072": "Suspecte",
    "00000008004E6311": "Référence proche",
}

REFERENCE_PAIR = ("00000008004AB09A", "00000008004E6311")
GAMBETTA_GATEWAY = "00000008004E608F"
GAMBETTA_LORA = "LORA:00:80:00:00:A0:00:9B:81"
ANTENNA_CHANGE_DATE = datetime(2026, 4, 24, tzinfo=PARIS)


def esc(v):
    return html.escape(str(v or ""))


def clean(v):
    return " ".join(str(v or "").replace("\n", " ").replace("\t", " ").replace("\xa0", " ").split()).strip()


def to_float(v):
    try:
        return float(str(v).replace(",", ".").replace(" ", "").strip())
    except Exception:
        return None


def parse_dt(v):
    v = clean(v)
    try:
        return datetime.strptime(v, "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
    except Exception:
        return None


def fmt_date(v):
    if not v:
        return "-"
    try:
        return v.astimezone(PARIS).strftime("%d/%m/%Y %H:%M:%S")
    except Exception:
        return str(v)


def fmt_num(v):
    if v is None:
        return "-"
    if isinstance(v, float):
        return f"{v:.1f}"
    return str(v)


def make_absolute_url(base_url, url):
    if not url:
        return ""
    url = html.unescape(str(url)).replace("&amp;", "&")
    if url.startswith("http"):
        return url
    if url.startswith("/"):
        return base_url.rstrip("/") + url
    return base_url.rstrip("/") + "/" + url.lstrip("/")


def find_valence_config():
    if not CONFIG:
        raise Exception("REQUEA_CONFIG vide")

    for c in CONFIG:
        url = str(c.get("url", "")).rstrip("/").lower()
        name = str(c.get("name", "")).lower()
        if "valenceromansagglo" in url or "valence" in name:
            return {
                "name": c.get("name", "Valence Romans"),
                "url": VALENCE_URL,
                "login": c.get("login", ""),
                "password": c.get("password", "")
            }

    c = CONFIG[0]
    return {
        "name": "Valence Romans",
        "url": VALENCE_URL,
        "login": c.get("login", ""),
        "password": c.get("password", "")
    }


def login(page, cluster):
    # Valence peut renvoyer une page vide au premier accès headless, alors on ne bloque pas
    # si l'URL applicative est atteinte. La vraie validation se fait ensuite sur le listing AJAX.
    start_urls = [
        f"{VALENCE_URL}/do/Network/iotGateway:list",
        VALENCE_URL,
    ]

    last_debug = {}

    for start_url in start_urls:
        print("Tentative accès Valence:", start_url)

        try:
            page.goto(start_url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(3500)

            body = clean(page.locator("body").inner_text())
            current_url = page.url

            # Cas qui fonctionnait déjà : accès à la route applicative.
            if "iotGateway:list" in current_url:
                print("Accès Valence OK:", current_url)
                return

            # Déjà connecté avec contenu visible.
            if (
                "Sign out" in body
                or "Déconnexion" in body
                or "Deconnexion" in body
                or "Export Excel" in body
                or "Liste des passerelles" in body
            ) and "Mot de passe oublié" not in body:
                print("Accès Valence OK:", current_url)
                return

            username = None
            password = None

            for selector in [
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
            ]:
                try:
                    loc = page.locator(selector)
                    if loc.count() > 0:
                        username = loc.first
                        break
                except Exception:
                    pass

            for selector in [
                'input[type="password"]:visible',
                'input[name*="password" i]:visible',
                'input[name*="pass" i]:visible',
                'input[id*="password" i]:visible',
                'input[id*="pass" i]:visible',
            ]:
                try:
                    loc = page.locator(selector)
                    if loc.count() > 0:
                        password = loc.first
                        break
                except Exception:
                    pass

            last_debug = {
                "tested_url": start_url,
                "current_url": current_url,
                "title": page.title(),
                "body_start": body[:1000],
                "input_count": page.locator("input").count(),
            }

            if not username or not password:
                continue

            username.fill(cluster.get("login", ""))
            password.fill(cluster.get("password", ""))
            page.wait_for_timeout(300)
            password.press("Enter")
            page.wait_for_timeout(7000)

            body = clean(page.locator("body").inner_text())

            if "Mot de passe oublié" in body or "Forgot your password" in body:
                raise Exception("Connexion refusée")

            print("Connexion Valence OK:", page.url)
            return

        except Exception as e:
            last_debug["exception"] = str(e)

    # On tente quand même la suite si la route applicative est accessible mais vide.
    # Si le listing ne remonte rien, l'erreur sera explicite dans collect_valence_gateways.
    print("Connexion non confirmée, tentative listing malgré tout:", json.dumps(last_debug, ensure_ascii=False))
    return


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
        const href = (el.getAttribute("href") || "").toLowerCase();

        if (cls.includes("disabled") || el.getAttribute("disabled") !== null) continue;

        if (
            txt === ">" ||
            txt === "›" ||
            cls.includes("rqnavnext") ||
            cls.includes("next") ||
            title.includes("suivant") ||
            title.includes("next") ||
            aria.includes("suivant") ||
            aria.includes("next") ||
            href.includes("listnav")
        ) {
            el.click();
            return true;
        }
    }

    return false;
}
""")
    if clicked:
        page.wait_for_timeout(1500)
    return clicked


def parse_gateway_listing(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    gateways = {}

    for tr in soup.find_all("tr"):
        tr_html = str(tr)
        if "iotGateway:get" not in tr_html:
            continue

        cells = [
            clean(td.get_text(" ", strip=True)).replace("\u00a0", " ")
            for td in tr.find_all("td", class_="rqtblcel")
        ]

        # Valence listing attendu :
        # Nom, Etat, Identifiant, Numéro série, Groupes, Etat connexion, Type support, ...
        if len(cells) < 19:
            continue

        gateway_id = ""
        for v in cells:
            if re.fullmatch(r"[0-9A-Fa-f]{16}", clean(v)):
                gateway_id = clean(v).upper()
                break

        if not gateway_id:
            continue

        status = cells[1] if len(cells) > 1 else ""
        if "Active" not in status:
            continue

        detail_url = ""
        onclick_cell = tr.find("td", onclick=True)
        if onclick_cell:
            m = re.search(r"RQ\.nav\.detail\('([^']*iotGateway:get[^']*)'", onclick_cell.get("onclick", ""))
            if m:
                detail_url = make_absolute_url(VALENCE_URL, m.group(1))

        if not detail_url:
            m = re.search(r"(/do/[^'\"<>\s]*iotGateway:get\?[^'\"<>\s]+)", tr_html)
            if m:
                detail_url = make_absolute_url(VALENCE_URL, m.group(1))

        gateways[gateway_id] = {
            "gateway_id": gateway_id,
            "name": cells[0],
            "status": status,
            "serial": cells[3] if len(cells) > 3 else "",
            "connection": cells[5] if len(cells) > 5 else "",
            "support_type": cells[6] if len(cells) > 6 else "",
            "owner": cells[7] if len(cells) > 7 else "",
            "height": cells[8] if len(cells) > 8 else "",
            "mount_type": cells[9] if len(cells) > 9 else "",
            "support_status": cells[10] if len(cells) > 10 else "",
            "description": cells[11] if len(cells) > 11 else "",
            "network": cells[12] if len(cells) > 12 else "",
            "city": cells[13] if len(cells) > 13 else "",
            "firmware": cells[14] if len(cells) > 14 else "",
            "sim": cells[15] if len(cells) > 15 else "",
            "ip": cells[16] if len(cells) > 16 else "",
            "created": cells[17] if len(cells) > 17 else "",
            "gps": cells[18] if len(cells) > 18 else "",
            "maps": cells[19] if len(cells) > 19 else "",
            "detail_url": detail_url,
        }

    return gateways


def collect_valence_gateways(page):
    ajax_payloads = []

    def on_response(response):
        try:
            url = response.url
            if "/ajax" in url or "iotGateway:list" in url:
                body = response.text()
                if "iotGateway:get" in body or "Liste des passerelles" in body or "rqtblcel" in body:
                    ajax_payloads.append(body)
                    print(f"Payload listing capturé: {len(body)} caractères depuis {url}")
        except Exception:
            pass

    page.on("response", on_response)

    page.goto(f"{VALENCE_URL}/do/Network/iotGateway:list", wait_until="domcontentloaded", timeout=60000)
    page.wait_for_timeout(12000)

    found = {}
    visited_signatures = set()

    for page_index in range(40):
        sources = [page.content()] + ajax_payloads

        page_total = 0
        for source in sources:
            page_gateways = parse_gateway_listing(source)
            page_total += len(page_gateways)

            for gid, gw in page_gateways.items():
                found[gid] = gw

        print(
            f"Listing Valence page {page_index + 1}: "
            f"{page_total} lignes actives parsées / total cumulé {len(found)}"
        )

        signature = "|".join(sorted(found.keys()))
        if signature in visited_signatures and page_index > 0:
            print("Arrêt pagination listing: signature déjà vue")
            break
        visited_signatures.add(signature)

        ajax_payloads.clear()

        if not click_next(page):
            print("Fin pagination listing")
            break

        page.wait_for_timeout(2500)

    try:
        page.remove_listener("response", on_response)
    except Exception:
        pass

    print(f"Total passerelles actives Valence trouvées: {len(found)}")
    return found


def messages_url_from_detail(detail_url):
    if not detail_url:
        return ""
    return detail_url.replace("iotGateway:get", "iotGateway:viewMessages")


def parse_message_rows(html_text):
    soup = BeautifulSoup(html_text, "html.parser")
    rows = []

    for tr in soup.find_all("tr"):
        cells = [clean(td.get_text(" ", strip=True)) for td in tr.find_all("td", class_="rqtblcel")]

        if len(cells) < 11:
            continue

        dt = parse_dt(cells[0])
        if not dt:
            continue

        rows.append({
            "time": dt,
            "offline": cells[1] if len(cells) > 1 else "",
            "dev_eui": cells[2].upper() if len(cells) > 2 else "",
            "network": cells[3] if len(cells) > 3 else "",
            "port": cells[4] if len(cells) > 4 else "",
            "dr": cells[5] if len(cells) > 5 else "",
            "redundancy": cells[6] if len(cells) > 6 else "",
            "sf": cells[7] if len(cells) > 7 else "",
            "seq": cells[8] if len(cells) > 8 else "",
            "rssi": to_float(cells[9]) if len(cells) > 9 else None,
            "snr": to_float(cells[10]) if len(cells) > 10 else None,
        })

    return rows


def read_gateway_messages(context, gateway):
    url = messages_url_from_detail(gateway.get("detail_url"))
    if not url:
        gateway["traffic_error"] = "detail_url absente"
        return []

    page = context.new_page()
    messages = []
    seen_keys = set()

    try:
        page.goto(url, wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(2500)

        if "iotDeviceMessage" not in page.content() and "DevEUI" not in page.locator("body").inner_text():
            # On garde quand même la tentative, certains HTML ne contiennent pas ce texte dans le raw.
            pass

        for page_num in range(MAX_MESSAGE_PAGES_PER_GATEWAY):
            page_rows = parse_message_rows(page.content())
            print(f"  messages page {page_num + 1}: {len(page_rows)}")

            for row in page_rows:
                key = (row["time"].isoformat(), row["dev_eui"], row["seq"])
                if key not in seen_keys:
                    seen_keys.add(key)
                    messages.append(row)

            if not page_rows:
                break

            oldest = min(r["time"] for r in page_rows)
            if oldest < NOW - timedelta(hours=24):
                break

            if not click_next(page):
                break

        page.close()
        return messages

    except Exception as e:
        gateway["traffic_error"] = str(e)
        try:
            page.close()
        except Exception:
            pass
        return messages


def stats_for_messages(messages):
    if not messages:
        return {
            "messages_sample": 0,
            "messages_1h": 0,
            "messages_24h": 0,
            "last_message": None,
            "dev_unique": 0,
            "rssi_avg": None,
            "rssi_weak_pct": 0,
            "snr_avg": None,
            "window_hours": 0,
        }

    one_hour = NOW - timedelta(hours=1)
    one_day = NOW - timedelta(hours=24)

    rssis = [m["rssi"] for m in messages if m["rssi"] is not None]
    snrs = [m["snr"] for m in messages if m["snr"] is not None]

    newest = max(m["time"] for m in messages)
    oldest = min(m["time"] for m in messages)
    window_hours = round(max((newest - oldest).total_seconds() / 3600, 0), 1)

    return {
        "messages_sample": len(messages),
        "messages_1h": sum(1 for m in messages if m["time"] >= one_hour),
        "messages_24h": sum(1 for m in messages if m["time"] >= one_day),
        "last_message": newest,
        "dev_unique": len(set(m["dev_eui"] for m in messages)),
        "rssi_avg": round(statistics.mean(rssis), 1) if rssis else None,
        "rssi_weak_pct": round(sum(1 for r in rssis if r <= -110) / len(rssis) * 100, 1) if rssis else 0,
        "snr_avg": round(statistics.mean(snrs), 1) if snrs else None,
        "window_hours": window_hours,
    }


def pct(value, base):
    if not base:
        return 0
    return round(value / base * 100, 1)


def label_for_gateway(gid):
    return TARGET_GATEWAYS.get(gid, "Standard")


def badge_class(row, median):
    r = pct(row["messages_sample"], median)
    if row["gateway_id"] in TARGET_GATEWAYS:
        return "ko" if r < 50 else "warn" if r < 80 else "focus"
    return "ko" if r < 50 else "warn" if r < 80 else "ok"


with sync_playwright() as p:
    browser = p.chromium.launch(headless=True)
    context = browser.new_context(viewport={"width": 1600, "height": 1000})
    page = context.new_page()

    cluster = find_valence_config()
    login(page, cluster)

    gateways = collect_valence_gateways(page)
    page.close()

    if len(gateways) <= 1:
        raise Exception(f"Collecte listing incorrecte: seulement {len(gateways)} passerelle(s) trouvée(s)")

    rows = []

    for gid, gw in gateways.items():
        print("Analyse trafic Valence", gid, "-", gw.get("name", ""))
        messages = read_gateway_messages(context, gw)
        row = {**gw, **stats_for_messages(messages)}
        row["role"] = label_for_gateway(gid)
        rows.append(row)

    context.close()
    browser.close()


rows = sorted(rows, key=lambda r: (r["gateway_id"] not in TARGET_GATEWAYS, -r["messages_sample"]))

counts = [r["messages_sample"] for r in rows if r["messages_sample"] > 0]
median_sample = statistics.median(counts) if counts else 0
avg_sample = round(statistics.mean(counts), 1) if counts else 0

if not counts:
    raise Exception("Aucune trame collectée sur les passerelles Valence")

target_rows = [r for r in rows if r["gateway_id"] in TARGET_GATEWAYS]

pair_a = next((r for r in rows if r["gateway_id"] == REFERENCE_PAIR[0]), None)
pair_b = next((r for r in rows if r["gateway_id"] == REFERENCE_PAIR[1]), None)
pair_ratio = pct(pair_a["messages_sample"], pair_b["messages_sample"]) if pair_a and pair_b else 0

gambetta = next((r for r in rows if r["gateway_id"] == GAMBETTA_GATEWAY), None)

os.makedirs("public", exist_ok=True)

html_page = f"""<!DOCTYPE html>
<html lang="fr">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Analyse trafic Valence Romans</title>
<style>
:root {{
    --ink:#08111f; --muted:#667085; --line:rgba(255,255,255,.62);
    --shadow:0 20px 70px rgba(31,41,55,.14), inset 0 1px 0 rgba(255,255,255,.70);
    --blue:#1473ff; --cyan:#00b8f5; --green:#16c784; --red:#ff3b5c; --orange:#ff9f0a; --violet:#7c3aed;
}}
*{{box-sizing:border-box}}
body{{margin:0;padding:22px;font-family:-apple-system,BlinkMacSystemFont,"SF Pro Display","Segoe UI",Arial,sans-serif;color:var(--ink);background:radial-gradient(circle at 8% 5%, rgba(20,115,255,.30), transparent 26%),radial-gradient(circle at 88% 8%, rgba(124,58,237,.24), transparent 28%),radial-gradient(circle at 56% 102%, rgba(0,184,245,.22), transparent 34%),linear-gradient(180deg,#ffffff 0%,#f7faff 46%,#edf5ff 100%);}}
.shell{{max-width:1680px;margin:0 auto}}
.hero,.panel{{position:relative;overflow:hidden;border:1px solid var(--line);background:linear-gradient(145deg,rgba(255,255,255,.48),rgba(255,255,255,.22));box-shadow:var(--shadow);backdrop-filter:blur(38px) saturate(210%);-webkit-backdrop-filter:blur(38px) saturate(210%);}}
.hero{{border-radius:38px;padding:30px;margin-bottom:22px}}
.panel{{border-radius:32px;padding:24px;margin-bottom:22px}}
.topbar{{display:flex;align-items:center;justify-content:space-between;gap:18px;flex-wrap:wrap}}
.brand{{display:flex;align-items:center;gap:16px}}
.logo{{width:66px;height:66px;border-radius:23px;display:grid;place-items:center;color:white;font-weight:900;font-size:20px;background:linear-gradient(145deg,#1473ff,#7c3aed)}}
.eyebrow{{font-size:12px;font-weight:850;letter-spacing:.10em;text-transform:uppercase;color:#2563eb;margin-bottom:8px}}
h1{{margin:0;font-size:46px;line-height:.95;font-weight:950;letter-spacing:-.06em}}
.subtitle{{color:var(--muted);font-size:15px;margin-top:10px;font-weight:600}}
.updated{{padding:12px 17px;border-radius:999px;background:rgba(255,255,255,.42);border:1px solid rgba(255,255,255,.72);color:#344054;font-size:14px;font-weight:800}}
.kpis{{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));gap:14px;margin-top:28px}}
.kpi{{position:relative;overflow:hidden;min-height:122px;padding:18px;border-radius:27px;color:white;box-shadow:0 18px 45px rgba(15,23,42,.14), inset 0 1px 0 rgba(255,255,255,.38)}}
.kpi-label{{font-size:13px;font-weight:850;opacity:.96}}
.kpi-value{{font-size:34px;line-height:1;font-weight:950;letter-spacing:-.05em;margin-top:8px}}
.kpi-sub{{margin-top:8px;font-size:12px;font-weight:700;opacity:.88}}
.g-blue{{background:linear-gradient(145deg,#1473ff,#58b8ff)}} .g-cyan{{background:linear-gradient(145deg,#00b8f5,#67e8f9)}} .g-green{{background:linear-gradient(145deg,#12b76a,#4ade80)}} .g-red{{background:linear-gradient(145deg,#f43f5e,#fb7185)}} .g-violet{{background:linear-gradient(145deg,#7c3aed,#a78bfa)}}
h2{{margin:0;font-size:30px;line-height:1.05;font-weight:950;letter-spacing:-.05em}}
.caption{{color:var(--muted);font-weight:650;font-size:14px;margin-top:7px}}
.table-wrap{{overflow:auto;border-radius:24px;border:1px solid rgba(255,255,255,.62);background:rgba(255,255,255,.28);margin-top:18px}}
table{{width:100%;min-width:1450px;border-collapse:collapse}}
th{{position:sticky;top:0;z-index:3;background:rgba(255,255,255,.72);backdrop-filter:blur(20px);-webkit-backdrop-filter:blur(20px);color:#475467;text-align:left;font-size:12px;text-transform:uppercase;letter-spacing:.035em;padding:14px}}
td{{padding:14px;border-bottom:1px solid rgba(148,163,184,.15);white-space:nowrap;font-size:13px}}
tr:hover{{background:rgba(255,255,255,.36)}}
.badge{{display:inline-flex;align-items:center;border-radius:999px;padding:7px 11px;font-size:12px;font-weight:850}}
.ok{{background:#dcfae6;color:#067647}} .warn{{background:#fef0c7;color:#b54708}} .ko{{background:#fee4e2;color:#b42318}} .focus{{background:#e0e7ff;color:#3730a3}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:14px;margin-top:18px}}
.card{{border-radius:24px;padding:18px;background:rgba(255,255,255,.36);border:1px solid rgba(255,255,255,.66);box-shadow:0 12px 34px rgba(15,23,42,.08)}}
.card-title{{font-size:15px;font-weight:900;color:#111827}} .card-num{{font-size:34px;font-weight:950;letter-spacing:-.05em;margin-top:8px}} .card-sub{{font-size:13px;color:var(--muted);font-weight:700;margin-top:6px}}
@media(max-width:900px){{body{{padding:10px}}.hero,.panel{{border-radius:24px;padding:16px}}h1{{font-size:31px}}h2{{font-size:24px}}.kpis{{grid-template-columns:repeat(2,1fr)}}.kpi-value{{font-size:28px}}}}
</style>
</head>
<body>
<div class="shell">
<section class="hero">
    <div class="topbar">
        <div class="brand"><div class="logo">VR</div><div><div class="eyebrow">Analyse trafic LoRaWAN</div><h1>Valence Romans</h1><div class="subtitle">Comparaison des trames collectées par passerelle, avec focus sur les sites signalés.</div></div></div>
        <div class="updated">Mise à jour · {NOW.strftime("%d/%m/%Y %H:%M")}</div>
    </div>
    <div class="kpis">
        <div class="kpi g-blue"><div class="kpi-label">Passerelles analysées</div><div class="kpi-value">{len(rows)}</div><div class="kpi-sub">actives Valence</div></div>
        <div class="kpi g-cyan"><div class="kpi-label">Médiane échantillon</div><div class="kpi-value">{median_sample}</div><div class="kpi-sub">trames récentes / passerelle</div></div>
        <div class="kpi g-green"><div class="kpi-label">Moyenne échantillon</div><div class="kpi-value">{avg_sample}</div><div class="kpi-sub">hors zéro</div></div>
        <div class="kpi g-red"><div class="kpi-label">Passerelles ciblées</div><div class="kpi-value">{len(target_rows)}</div><div class="kpi-sub">suspectes + référence</div></div>
        <div class="kpi g-violet"><div class="kpi-label">Pages / passerelle</div><div class="kpi-value">{MAX_MESSAGE_PAGES_PER_GATEWAY}</div><div class="kpi-sub">limite de collecte</div></div>
    </div>
</section>

<section class="panel">
<h2>Focus passerelles signalées</h2>
<div class="caption">Les passerelles suspectes sont comparées à la médiane du cluster et à la référence de proximité.</div>
<div class="table-wrap"><table>
<tr><th>Passerelle</th><th>Rôle</th><th>Site</th><th>Ville</th><th>Trames</th><th>% médiane</th><th>DevEUI</th><th>RSSI moy.</th><th>% RSSI faible</th><th>SNR moy.</th><th>Dernière trame</th><th>Firmware</th><th>SIM</th></tr>
"""

for r in target_rows:
    cls = badge_class(r, median_sample)
    html_page += f"""
<tr>
<td><strong>{esc(r["gateway_id"])}</strong></td>
<td><span class="badge {cls}">{esc(r["role"])}</span></td>
<td>{esc(r["name"])}</td><td>{esc(r["city"])}</td>
<td>{r["messages_sample"]}</td><td>{pct(r["messages_sample"], median_sample)}%</td>
<td>{r["dev_unique"]}</td><td>{fmt_num(r["rssi_avg"])}</td><td>{r["rssi_weak_pct"]}%</td><td>{fmt_num(r["snr_avg"])}</td>
<td>{fmt_date(r["last_message"])}</td><td>{esc(r["firmware"])}</td><td>{esc(r["sim"])}</td>
</tr>
"""

html_page += f"""
</table></div></section>

<section class="panel">
<h2>Comparaison de proximité</h2>
<div class="caption">Comparaison directe entre {REFERENCE_PAIR[0]} et la référence proche {REFERENCE_PAIR[1]}.</div>
<div class="cards">
<div class="card"><div class="card-title">{REFERENCE_PAIR[0]}</div><div class="card-num">{pair_a["messages_sample"] if pair_a else "-"}</div><div class="card-sub">trames échantillon</div></div>
<div class="card"><div class="card-title">{REFERENCE_PAIR[1]}</div><div class="card-num">{pair_b["messages_sample"] if pair_b else "-"}</div><div class="card-sub">trames échantillon référence</div></div>
<div class="card"><div class="card-title">Ratio suspecte / référence</div><div class="card-num">{pair_ratio}%</div><div class="card-sub">plus le ratio est bas, plus l’écart est marqué</div></div>
</div></section>

<section class="panel">
<h2>Impact antenne Gambetta</h2>
<div class="caption">Site {GAMBETTA_LORA} · remplacement antenne le 24/04/2026. L'historique avant/après nécessite des pages messages couvrant cette date.</div>
<div class="cards">
<div class="card"><div class="card-title">Passerelle Gambetta</div><div class="card-num">{GAMBETTA_GATEWAY}</div><div class="card-sub">{esc(gambetta["name"]) if gambetta else "non trouvée"}</div></div>
<div class="card"><div class="card-title">Trames échantillon</div><div class="card-num">{gambetta["messages_sample"] if gambetta else "-"}</div><div class="card-sub">collecte récente</div></div>
<div class="card"><div class="card-title">Avant / après antenne</div><div class="card-num">À historiser</div><div class="card-sub">à activer avec archive quotidienne</div></div>
</div></section>

<section class="panel">
<h2>Toutes les passerelles Valence</h2>
<div class="caption">Classement par volume de trames collectées sur l’échantillon récent.</div>
<div class="table-wrap"><table>
<tr><th>Passerelle</th><th>Rôle</th><th>Site</th><th>Ville</th><th>Trames</th><th>% médiane</th><th>1h</th><th>24h</th><th>Fenêtre h</th><th>DevEUI</th><th>RSSI moy.</th><th>RSSI faible</th><th>SNR moy.</th><th>Connexion</th><th>Firmware</th><th>SIM</th><th>IP</th><th>Erreur</th></tr>
"""

for r in sorted(rows, key=lambda x: x["messages_sample"]):
    cls = badge_class(r, median_sample)
    html_page += f"""
<tr>
<td><strong>{esc(r["gateway_id"])}</strong></td>
<td><span class="badge {cls}">{esc(r["role"])}</span></td>
<td>{esc(r["name"])}</td><td>{esc(r["city"])}</td>
<td>{r["messages_sample"]}</td><td>{pct(r["messages_sample"], median_sample)}%</td><td>{r["messages_1h"]}</td><td>{r["messages_24h"]}</td><td>{r["window_hours"]}</td>
<td>{r["dev_unique"]}</td><td>{fmt_num(r["rssi_avg"])}</td><td>{r["rssi_weak_pct"]}%</td><td>{fmt_num(r["snr_avg"])}</td>
<td>{esc(r["connection"])}</td><td>{esc(r["firmware"])}</td><td>{esc(r["sim"])}</td><td>{esc(r["ip"])}</td><td>{esc(r.get("traffic_error", ""))}</td>
</tr>
"""

html_page += """
</table></div></section>
</div></body></html>
"""

with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
    f.write(html_page)

print(f"Rapport Valence généré : {len(rows)} passerelles analysées")
print(f"Sortie : {OUTPUT_FILE}")
