from playwright.sync_api import sync_playwright
from datetime import datetime
from zoneinfo import ZoneInfo
import os, json, html, re

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

    if not m:
        return None

    try:
        return datetime.strptime(m.group(1), "%d/%m/%Y %H:%M:%S").replace(tzinfo=PARIS)
    except Exception:
        return None


def parse_last_connection_from_html(html_text):
    decoded = html.unescape(str(html_text or ""))

    patterns = [
        r"Derni[eè]re\s+connexion[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Derniere\s+connexion[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
        r"Last\s+connection[^0-9]*([0-9]{2}/[0-9]{2}/[0-9]{4}\s+[0-9]{2}:[0-9]{2}:[0-9]{2})",
    ]

    for pattern in patterns:
        m = re.search(pattern, decoded, re.I | re.S)
        if m:
            return parse_requea_date(m.group(1))

    text = strip_tags(decoded)

    for pattern in patterns:
        m = re.search(pattern, text, re.I | re.S)
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
            return f"{m.group(1)}, {m.group(2)}"

    return ""


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
    page.wait_for_timeout(4000)

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
    page.wait_for_timeout(10000)

    body = page.locator("body").inner_text()

    if "Mot de passe oublié" in body or "Forgot your password" in body:
        raise Exception("Connexion refusée")


def extract_detail_url_from_html(row_html, base_url):
    decoded = html.unescape(row_html)

    patterns = [
        r"(/do/iotGateway:get\?sysId=[^'\"&<>\s]+[^'\"<>\s]*)",
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
        if "mtcdt" in v.lower():
            firmware = v
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
            href = row.locator("a").first.get_attribute("href")
            detail_url = make_absolute_url(cluster["url"], href)
        except Exception:
            pass

        try:
            if not detail_url:
                onclick = row.get_attribute("onclick") or ""
                match = re.search(r"RQ\.nav\.(?:detail|go)\('([^']+)'", onclick)
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
        page.wait_for_timeout(7000)

    return clicked


def read_connection_date(context, cluster, gateway):
    detail_url = gateway.get("detail_url") or ""

    p = context.new_page()

    try:
        if detail_url:
            p.goto(detail_url, wait_until="domcontentloaded", timeout=60000)
            p.wait_for_timeout(6000)

            body_text = p.locator("body").inner_text()
            html_detail = p.content()

            last = parse_last_connection_from_html(body_text)

            if not last:
                last = parse_last_connection_from_html(html_detail)

            gps = geoloc_from_text(body_text)

            if gps:
                gateway["geolocation"] = gps

            if last:
                print("DATE TROUVEE", gateway["name"], last.strftime("%d/%m/%Y %H:%M:%S"))

            p.close()
            return last

    except Exception:
        pass

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
                f'{cluster["url"]}/page/Network_Gateways',
                wait_until="domcontentloaded",
                timeout=60000
            )

            page.wait_for_timeout(12000)

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
                connection_date = read_connection_date(context, cluster, gateway)

                if connection_date:
                    gateway["last_connection"] = connection_date.isoformat()

                if not gateway["down"]:
                    gateway["connected_since"] = gateway["last_connection"]

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


active_gateways = [g for g in gateways if g["status"] == "Active"]

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


def fmt_hours(v):
    try:
        v = float(v)
    except Exception:
        return "-"
    if v <= 0:
        return "0 h"
    if v < 1:
        return f"{round(v * 60)} min"
    return f"{v:.1f} h"

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
    background:rgba(255,255,255,.43);border:1px solid rgba(255,255,255,.66);
    box-shadow:inset 0 1px 0 rgba(255,255,255,.78),0 14px 30px rgba(15,23,42,.06);
}}
.slider{{position:absolute;top:7px;left:7px;height:calc(100% - 14px);border-radius:999px;background:linear-gradient(145deg,#1677ff,#7c3aed);box-shadow:0 10px 24px rgba(22,119,255,.26);transition:transform .28s cubic-bezier(.2,.8,.2,1),width .28s cubic-bezier(.2,.8,.2,1);}}
.seg-btn{{position:relative;z-index:2;border:0;background:transparent;border-radius:999px;padding:11px 17px;color:#334155;font-weight:850;white-space:nowrap;cursor:pointer;}}
.seg-btn.active{{color:white;}}
.table-wrap{{overflow:auto;border-radius:24px;border:1px solid rgba(255,255,255,.62);background:rgba(255,255,255,.28);}}
table{{width:100%;min-width:1450px;border-collapse:collapse;}}
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
</style>
<script>
function initSegmented() {{
    const buttons = Array.from(document.querySelectorAll(".seg-btn"));
    const slider = document.querySelector(".slider");
    function move(btn) {{
        if (!slider || !btn) return;
        slider.style.width = btn.offsetWidth + "px";
        slider.style.transform = `translateX(${{btn.offsetLeft}}px)`;
    }}
    function apply(btn) {{
        const cluster = btn.dataset.cluster;
        document.querySelectorAll(".gateway-row").forEach(row => {{
            row.style.display = cluster === "ALL" || row.dataset.cluster === cluster ? "" : "none";
        }});
        move(btn);
    }}
    buttons.forEach(btn => {{
        btn.addEventListener("click", () => {{
            buttons.forEach(b => b.classList.remove("active"));
            btn.classList.add("active");
            apply(btn);
        }});
    }});
    const active = document.querySelector(".seg-btn.active");
    if (active) apply(active);
    window.addEventListener("resize", () => move(document.querySelector(".seg-btn.active")));
}}
window.addEventListener("load", initSegmented);
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
        <div class="kpi g-blue"><div class="kpi-icon"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/></svg></div><div class="kpi-label">Clusters</div><div class="kpi-value">{len(clusters)}</div></div>
        <div class="kpi g-cyan"><div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M6 20h12M12 20V10"/><path d="M8 10a4 4 0 0 1 8 0M5 7a8 8 0 0 1 14 0"/></svg></div><div class="kpi-label">Passerelles</div><div class="kpi-value">{total}</div></div>
        <div class="kpi g-green"><div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M20 6 9 17l-5-5"/></svg></div><div class="kpi-label">Connectées</div><div class="kpi-value">{ok}</div></div>
        <div class="kpi g-red"><div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M12 8v5M12 17h.01"/><path d="M10.3 4.3 2.8 17.2A2 2 0 0 0 4.5 20h15a2 2 0 0 0 1.7-2.8L13.7 4.3a2 2 0 0 0-3.4 0Z"/></svg></div><div class="kpi-label">Déconnectées</div><div class="kpi-value">{down}</div></div>
        <div class="kpi g-orange"><div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M4 19V5M4 19h16"/><path d="m7 15 4-4 3 3 5-7"/></svg></div><div class="kpi-label">Service</div><div class="kpi-value">{service}%</div></div>
        <div class="kpi g-violet"><div class="kpi-icon"><svg viewBox="0 0 24 24"><path d="M14.7 6.3a4 4 0 0 0-5 5L4 17v3h3l5.7-5.7a4 4 0 0 0 5-5l-3 3-3-3 3-3Z"/></svg></div><div class="kpi-label">Maintenance</div><div class="kpi-value">{maintenance}</div></div>
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
    <div class="section-head"><div><h2>Filtre clusters</h2><div class="section-caption">Sélection fluide avec indicateur glissant.</div></div></div>
    <div class="filter-shell"><div class="filter"><div class="slider"></div><button class="seg-btn active" data-cluster="ALL">Tous</button>
"""

for c in clusters:
    html_page += f'<button class="seg-btn" data-cluster="{esc(c)}">{esc(c)}</button>\n'

html_page += """
    </div></div>
</section>
<section class="panel">
    <div class="section-head"><div><h2>Passerelles HS</h2><div class="section-caption">Priorisation maintenance et durée d’indisponibilité.</div></div></div>
    <div class="table-wrap"><table>
        <tr><th>Cluster</th><th>Passerelle</th><th>Ville</th><th>GPS</th><th>Connexion</th><th>Dernière connexion</th><th>HS depuis</th><th>Durée HS</th><th>Service 24h</th><th>Firmware</th></tr>
"""

for g in active_gateways:
    if not g["down"]:
        continue
    row_class = "maintenance" if g["maintenance"] else "down"
    html_page += f"""
        <tr class="gateway-row {row_class}" data-cluster="{esc(g["cluster"])}">
            <td><strong>{esc(g["cluster"])}</strong></td><td><strong>{esc(g["name"])}</strong></td><td>{esc(g["city"])}</td><td>{esc(g["geolocation"])}</td>
            <td><span class="badge ko">{esc(g["connection"])}</span></td><td>{fmt_date(g["last_connection"])}</td><td>{fmt_date(g["down_since"])}</td><td>{fmt_hours(g["down_hours"])}</td><td>{g["service_24h"]}%</td><td>{esc(g["firmware"])}</td>
        </tr>
"""

html_page += """
    </table></div>
</section>
<section class="panel">
    <div class="section-head"><div><h2>Toutes les passerelles</h2><div class="section-caption">Inventaire consolidé des passerelles actives.</div></div></div>
    <div class="table-wrap"><table>
        <tr><th>Cluster</th><th>Passerelle</th><th>Ville</th><th>GPS</th><th>Statut</th><th>Connexion</th><th>Connecté depuis</th><th>Dernière connexion</th><th>Firmware</th><th>ID</th></tr>
"""

for g in active_gateways:
    badge = "ko" if g["down"] else "ok"
    row_class = "maintenance" if g["maintenance"] else ("down" if g["down"] else "")
    connected_since = g["connected_since"] if not g["down"] else None
    html_page += f"""
        <tr class="gateway-row {row_class}" data-cluster="{esc(g["cluster"])}">
            <td><strong>{esc(g["cluster"])}</strong></td><td><strong>{esc(g["name"])}</strong></td><td>{esc(g["city"])}</td><td>{esc(g["geolocation"])}</td>
            <td><span class="badge ok">{esc(g["status"])}</span></td><td><span class="badge {badge}">{esc(g["connection"])}</span></td><td>{fmt_date(connected_since)}</td><td>{fmt_date(g["last_connection"])}</td><td>{esc(g["firmware"])}</td><td>{esc(g["gateway_id"])}</td>
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
