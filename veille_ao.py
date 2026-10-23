#!/usr/bin/env python3
"""Veille AO énergie : BOAMP + TED -> veille_ao.html + ao.json
Usage : python veille_ao.py [--jours 30]     (bibliothèque standard uniquement)"""
import argparse, html, json, re, urllib.request, urllib.parse
from datetime import date, timedelta

MOTS_CLES = ["fourniture", "électricité", "enr", "agrégation"]
# Un avis n'est gardé que s'il contient un terme énergie (évite "fourniture de bureau")
ENERGIE = re.compile(r"[ée]lectricit|[ée]nergie|\benr\b|renouvelable|agr[ée]gation|garanties? d.origine|\bppa\b|obligation d.achat", re.I)
GRDS = ["Enedis", "GRDF", "RTE", "Strasbourg Électricité Réseaux", "Électricité de Strasbourg", "SRD",
        "Gérédis", "SICAE", "Sorégies", "URM", "Régaz", "R-GDS", "Bordeaux Métropole Énergies"]
UNITES = {"kwh": 0.001, "mwh": 1, "gwh": 1000, "twh": 1_000_000}
ESP = r"[\d \u202f\u00a0]"


def get(url, body=None):
    req = urllib.request.Request(url, data=body, headers={"User-Agent": "veille-ao/1.0", "Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def ft(v):  # TED renvoie des dicts {langue: texte} ou des listes
    if isinstance(v, dict):
        v = v.get("fra") or next(iter(v.values()), "")
    if isinstance(v, list):
        return " ".join(ft(x) for x in v)
    return str(v or "")


def infos(txt):
    t = html.unescape(re.sub(r"<[^>]+>", " ", txt))
    sites = [int(re.sub(r"\D", "", m)) for m in re.findall(r"(\d" + ESP + r"*)\s*(?:sites?|points? de (?:livraison|comptage)|PDL|PRM|PCE)\b", t, re.I)]
    vols = []
    for n, u in re.findall(r"(\d[\d \u202f\u00a0.,]*)\s*(TWh|GWh|MWh|kWh)\b", t, re.I):
        try:
            vols.append(float(re.sub(r"[ \u202f\u00a0]", "", n).replace(",", ".")) * UNITES[u.lower()])
        except ValueError:
            pass
    segs = ["C" + x for x in sorted(set(re.findall(r"\bC([1-5])\b", t)))]
    segs += sorted({m.upper() for m in re.findall(r"\b(HTA|HTB)\b", t, re.I)})
    segs += sorted({"Tarif " + m.lower() for m in re.findall(r"tarifs? (bleu|jaune|vert)", t, re.I)})
    v = max(vols) if vols else 0
    return {"sites": max(sites) if sites else "",
            "segments": segs,
            "volume": (f"{v/1000:,.1f} GWh" if v >= 1000 else f"{v:,.0f} MWh").replace(",", " ") if v else "",
            "grd": [g for g in GRDS if re.search(r"\b" + re.escape(g) + r"\b", t, re.I)]}


def entree(src, id_, titre, client, dt, lim, lien, txt):
    return {"src": src, "id": id_, "titre": titre, "client": client, "date": dt, "limite": lim, "lien": lien, **infos(titre + " " + txt)}


def boamp(jours):
    base = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
    depuis = (date.today() - timedelta(days=jours)).isoformat()
    out = {}
    for mc in MOTS_CLES:
        p = {"q": mc, "where": f"dateparution >= date'{depuis}'", "order_by": "dateparution desc", "limit": 100}
        for r in get(base + "?" + urllib.parse.urlencode(p)).get("results", []):
            txt = " ".join(str(r.get(k) or "") for k in ("objet", "descripteur_libelle", "donnees"))
            if ENERGIE.search(txt):
                id_ = r.get("idweb") or r.get("id")
                out[id_] = entree("BOAMP", id_, r.get("objet") or "(sans titre)", r.get("nomacheteur") or "",
                                  (r.get("dateparution") or "")[:10], (r.get("datelimitereponse") or "")[:10],
                                  r.get("url_avis") or "https://www.boamp.fr", txt)
    return list(out.values())


def ted(jours):
    depuis = (date.today() - timedelta(days=jours)).strftime("%Y%m%d")
    q = f"buyer-country=FRA AND classification-cpv IN (09310000 65300000 09300000 09330000) AND publication-date>={depuis}"
    body = json.dumps({"query": q, "limit": 100, "page": 1,
                       "fields": ["publication-number", "notice-title", "buyer-name", "publication-date", "description-lot"]}).encode()
    out = []
    for n in get("https://api.ted.europa.eu/v3/notices/search", body).get("notices", []):
        num = n.get("publication-number", "")
        out.append(entree("TED", num, ft(n.get("notice-title")) or num, ft(n.get("buyer-name")),
                          ft(n.get("publication-date"))[:10], "", f"https://ted.europa.eu/fr/notice/-/detail/{num}",
                          ft(n.get("description-lot"))))
    return out


PAGE = """<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veille AO énergie</title><style>
:root{--bg:#fafaf7;--fg:#1d1d1b;--mu:#6b6b66;--ca:#fff;--bo:#e2e0d8;--ac:#0b6e4f}
@media(prefers-color-scheme:dark){:root{--bg:#161614;--fg:#ecebe6;--mu:#9a9990;--ca:#1f1f1c;--bo:#34332e;--ac:#4cc49a}}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.45 system-ui,sans-serif}
main{max-width:900px;margin:0 auto;padding:24px 16px}h1{font-size:20px;margin:0 0 12px}
.bar{display:flex;gap:8px;margin-bottom:16px}.bar input,.bar select{padding:8px 10px;border:1px solid var(--bo);border-radius:8px;background:var(--ca);color:var(--fg);font:inherit}.bar input{flex:1}
ul{list-style:none;padding:0;margin:0}li{background:var(--ca);border:1px solid var(--bo);border-radius:10px;padding:12px 14px;margin-bottom:8px}
li a{color:var(--ac);font-weight:600;text-decoration:none}.m{color:var(--mu);font-size:13px;margin:2px 0 8px}
.i{display:flex;flex-wrap:wrap;gap:6px 14px;font-size:13px}.c b{color:var(--mu);font-weight:500;margin-right:3px}.off{opacity:.45}
</style><main><h1>Veille AO énergie · <span id="n">%%N%%</span> avis · mis à jour le %%DATE%%</h1>
<div class="bar"><input id="q" placeholder="Filtrer (client, mot-clé…)"><select id="s"><option value="">Toutes sources</option><option>BOAMP</option><option>TED</option></select></div>
<ul id="l">%%ROWS%%</ul></main><script>
const q=document.getElementById('q'),s=document.getElementById('s'),li=[...document.querySelectorAll('#l li')];
function f(){let k=0;li.forEach(e=>{const ok=e.dataset.t.includes(q.value.toLowerCase())&&(!s.value||e.dataset.s===s.value);e.hidden=!ok;k+=ok});document.getElementById('n').textContent=k}
q.oninput=s.onchange=f;</script></html>"""


def rendu(items):
    def chip(l, v):
        return f'<span class="c{"" if v else " off"}"><b>{l}</b>{html.escape(str(v)) if v else "—"}</span>'
    rows = "".join(
        f'<li data-t="{html.escape((a["titre"] + " " + a["client"]).lower())}" data-s="{a["src"]}">'
        f'<a href="{html.escape(a["lien"])}" target="_blank" rel="noopener">{html.escape(a["titre"])}</a>'
        f'<div class="m">{a["src"]} · publié le {a["date"]} · limite {a["limite"] or "—"}</div>'
        f'<div class="i">{chip("Client", a["client"])}{chip("GRD", ", ".join(a["grd"]))}{chip("Sites", a["sites"])}'
        f'{chip("Segments", ", ".join(a["segments"]))}{chip("Volume", a["volume"])}</div></li>' for a in items)
    return PAGE.replace("%%ROWS%%", rows).replace("%%N%%", str(len(items))).replace("%%DATE%%", date.today().strftime("%d/%m/%Y"))


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jours", type=int, default=30)
    a = ap.parse_args()
    items = []
    for nom, fn in (("BOAMP", boamp), ("TED", ted)):
        try:
            r = fn(a.jours)
            print(f"{nom} : {len(r)} avis")
            items += r
        except Exception as e:
            print(f"{nom} : échec ({e})")
    items.sort(key=lambda x: x["date"], reverse=True)
    json.dump(items, open("ao.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open("veille_ao.html", "w", encoding="utf-8").write(rendu(items))
    print("-> veille_ao.html / ao.json")
