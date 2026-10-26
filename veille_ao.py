"""Veille AO : BOAMP + TED + flux RSS -> veille_ao.html + ao.json (réglages dans config.json)
Usage : python veille_ao.py [--jours N]     (bibliothèque standard uniquement)"""
import argparse, html, json, re, unicodedata, urllib.parse, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, timedelta
from email.utils import parsedate_to_datetime

DEFAUT = {
    "jours": 30,
    "sources": ["boamp", "ted", "rss"],
    "mots_cles": ["fourniture", "électricité", "enr", "agrégation"],
    "termes_requis": ["électricité", "énergie", "enr", "renouvelable", "agrégation", "garantie d'origine", "ppa", "obligation d'achat"],
    "termes_exclus": [],
    "cpv_ted": ["09310000", "65300000", "09300000", "09330000"],
    "flux_rss": [],
    "grd": ["Enedis", "GRDF", "RTE", "Strasbourg Électricité Réseaux", "Électricité de Strasbourg", "SRD", "Gérédis",
            "SICAE", "Sorégies", "URM", "Régaz", "R-GDS", "Bordeaux Métropole Énergies"],
}
UNITES = {"kwh": 0.001, "mwh": 1, "gwh": 1000, "twh": 1_000_000}
ESP = r"[\d \u202f\u00a0]"


def charger_config():
    cfg = dict(DEFAUT)
    try:
        cfg.update(json.load(open("config.json", encoding="utf-8")))
    except FileNotFoundError:
        pass
    return cfg


def norm(s):
    return "".join(c for c in unicodedata.normalize("NFD", str(s).lower()) if unicodedata.category(c) != "Mn")


def trouve(terme, texte_norm):  # mots courts (enr, ppa…) : mot entier ; sinon : sous-chaîne
    t = norm(terme)
    return bool(re.search(r"\b" + re.escape(t) + r"\b", texte_norm)) if len(t) <= 4 else t in texte_norm


def garde(txt, cfg):
    n = norm(txt)
    if any(trouve(x, n) for x in cfg["termes_exclus"]):
        return False
    return not cfg["termes_requis"] or any(trouve(x, n) for x in cfg["termes_requis"])


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


def jour(s):
    try:
        return parsedate_to_datetime(s).date().isoformat()
    except Exception:
        return str(s)[:10]


def infos(txt, grds):
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
    return {"sites": max(sites) if sites else None, "segments": segs,
            "volume": (f"{v/1000:,.1f} GWh" if v >= 1000 else f"{v:,.0f} MWh").replace(",", " ") if v else "",
            "volume_mwh": round(v) if v else None,
            "grd": [g for g in grds if re.search(r"\b" + re.escape(g) + r"\b", t, re.I)]}


def entree(cfg, src, id_, titre, client, dt, lim, lien, txt):
    return {"src": src, "id": id_, "titre": titre, "client": client, "date": dt, "limite": lim, "lien": lien, **infos(titre + " " + txt, cfg["grd"])}


def boamp(cfg):
    base = "https://boamp-datadila.opendatasoft.com/api/explore/v2.1/catalog/datasets/boamp/records"
    depuis = (date.today() - timedelta(days=cfg["jours"])).isoformat()
    out = {}
    for mc in cfg["mots_cles"]:
        p = {"q": mc, "where": f"dateparution >= date'{depuis}'", "order_by": "dateparution desc", "limit": 100}
        try:
            res = get(base + "?" + urllib.parse.urlencode(p)).get("results", [])
        except Exception as e:
            print(f"BOAMP « {mc} » : échec ({e})")
            continue
        for r in res:
            txt = " ".join(str(r.get(k) or "") for k in ("objet", "descripteur_libelle", "donnees"))
            if garde(txt, cfg):
                id_ = r.get("idweb") or r.get("id")
                out[id_] = entree(cfg, "BOAMP", id_, r.get("objet") or "(sans titre)", r.get("nomacheteur") or "",
                                  (r.get("dateparution") or "")[:10], (r.get("datelimitereponse") or "")[:10],
                                  r.get("url_avis") or "https://www.boamp.fr", txt)
    return list(out.values())


def ted(cfg):
    depuis = (date.today() - timedelta(days=cfg["jours"])).strftime("%Y%m%d")
    base = f"buyer-country=FRA AND publication-date>={depuis}"
    reqs = [base + " AND classification-cpv IN (" + " ".join(cfg["cpv_ted"]) + ")"] if cfg["cpv_ted"] else []
    reqs += [base + f' AND FT ~ "{k}"' for k in cfg["mots_cles"]]
    out = {}
    for q in reqs:
        body = json.dumps({"query": q, "limit": 100, "page": 1,
                           "fields": ["publication-number", "notice-title", "buyer-name", "publication-date", "description-lot"]}).encode()
        try:
            notices = get("https://api.ted.europa.eu/v3/notices/search", body).get("notices", [])
        except Exception as e:
            print(f"TED : requête ignorée ({str(e)[:60]}) : {q[-45:]}")
            continue
        for n in notices:
            num, titre, desc = n.get("publication-number", ""), ft(n.get("notice-title")), ft(n.get("description-lot"))
            if num not in out and garde(titre + " " + desc, cfg):
                out[num] = entree(cfg, "TED", num, titre or num, ft(n.get("buyer-name")), ft(n.get("publication-date"))[:10], "",
                                  f"https://ted.europa.eu/fr/notice/-/detail/{num}", desc)
    return list(out.values())


def rss(cfg):  # flux RSS/Atom de votre choix (alertes de profils acheteurs, agrégateurs…)
    out = []
    for u in cfg["flux_rss"]:
        try:
            req = urllib.request.Request(u, headers={"User-Agent": "veille-ao/1.0"})
            root = ET.fromstring(urllib.request.urlopen(req, timeout=30).read())
        except Exception as e:
            print(f"RSS {u[:50]} : échec ({e})")
            continue
        for it in root.iter():
            if it.tag.split("}")[-1] not in ("item", "entry"):
                continue
            d = {c.tag.split("}")[-1]: (c.text or c.get("href") or "") for c in it}
            titre, desc = d.get("title", ""), d.get("description") or d.get("summary") or ""
            n = norm(titre + " " + desc)
            if garde(titre + " " + desc, cfg) and any(trouve(k, n) for k in cfg["mots_cles"]):
                out.append(entree(cfg, "RSS", d.get("guid") or d.get("link", ""), titre, "",
                                  jour(d.get("pubDate") or d.get("published") or d.get("updated") or ""), "", d.get("link", ""), desc))
    return out


SOURCES = {"boamp": boamp, "ted": ted, "rss": rss}

PAGE = r"""<!doctype html><html lang="fr"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Veille AO énergie</title><style>
:root{--bg:#f6f5f1;--fg:#1d1d1b;--mu:#6b6b66;--ca:#fff;--bo:#e2e0d8;--ac:#0b6e4f;--hot:#c2410c}
@media(prefers-color-scheme:dark){:root{--bg:#141412;--fg:#ecebe6;--mu:#9a9990;--ca:#1e1e1b;--bo:#34332e;--ac:#4cc49a;--hot:#fb923c}}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.4 system-ui,sans-serif}main{max-width:1280px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0}.sub{color:var(--mu);margin:4px 0 14px;display:flex;flex-wrap:wrap;gap:6px;align-items:center}
.k{background:var(--ca);border:1px solid var(--bo);border-radius:99px;padding:1px 10px;font-size:12px;color:var(--fg)}
.kpi{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin-bottom:14px}
.kpi div{background:var(--ca);border:1px solid var(--bo);border-radius:10px;padding:10px 14px}.kpi b{display:block;font-size:24px;color:var(--ac)}.kpi span{color:var(--mu);font-size:12px}
.f{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:8px;margin-bottom:14px;align-items:end}
.f label{font-size:11px;color:var(--mu);display:flex;flex-direction:column;gap:2px}
input,select,button{padding:6px 8px;border:1px solid var(--bo);border-radius:8px;background:var(--ca);color:var(--fg);font:inherit}
.w{overflow:auto;border:1px solid var(--bo);border-radius:10px;background:var(--ca);max-height:75vh}
table{border-collapse:collapse;width:100%}th,td{padding:8px 10px;border-bottom:1px solid var(--bo);text-align:left;vertical-align:top}
th{position:sticky;top:0;background:var(--ca);cursor:pointer;white-space:nowrap;font-size:12px;color:var(--mu)}
td a{color:var(--ac);font-weight:600;text-decoration:none}td.t{min-width:320px}.n{text-align:right;white-space:nowrap}
.b{font-size:11px;font-weight:600;padding:2px 7px;border-radius:6px}.BOAMP{background:#dbeafe;color:#1e40af}.TED{background:#fef3c7;color:#92400e}.RSS{background:#e9d5ff;color:#6b21a8}
.p{display:block;font-size:11px;color:var(--mu)}.hot{color:var(--hot);font-weight:600}
</style><main><h1>Veille AO énergie</h1>
<div class="sub">Mis à jour le <span id="maj"></span> · mots-clés : <span id="kw"></span></div>
<div class="kpi"><div><b id="k1"></b><span>avis affichés</span></div><div><b id="k2"></b><span>encore ouverts</span></div><div><b id="k3"></b><span>clôture sous 7 jours</span></div><div><b id="k4"></b><span>avec GRD identifié</span></div></div>
<div class="f"><label>Recherche (virgule = OU)<input id="q"></label><label>Source<select id="s"></select></label><label>GRD<select id="g"></select></label><label>Client<input id="c"></label>
<label>Publié du<input type="date" id="pf"></label><label>au<input type="date" id="pt"></label><label>Limite du<input type="date" id="lf"></label><label>au<input type="date" id="lt"></label>
<label><span><input type="checkbox" id="h" checked> Masquer les clôturés</span></label><button id="r">Réinitialiser</button></div>
<div class="w"><table><thead><tr id="th"></tr></thead><tbody id="tb"></tbody></table></div></main><script>
const D=%%DATA%%,C=%%CFG%%,$=i=>document.getElementById(i),T=new Date().toISOString().slice(0,10);
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const nm=s=>String(s??'').toLowerCase().normalize('NFD').replace(/[\u0300-\u036f]/g,'');
const fr=d=>d?d.split('-').reverse().join('/'):'—',left=d=>Math.round((new Date(d)-new Date(T))/864e5);
const COLS=[['date','Publié'],['limite','Limite'],['src','Source'],['titre','Titre'],['client','Client'],['grd','GRD'],['sites','Sites'],['segments','Segments'],['volume_mwh','Volume']];
let sk='date',sd=-1;
$('maj').textContent=C.maj;$('kw').innerHTML=C.mots_cles.map(k=>`<span class="k">${esc(k)}</span>`).join('');
const opt=(id,l,all)=>{$(id).innerHTML=`<option value="">${all}</option>`+[...new Set(l)].filter(Boolean).sort().map(x=>`<option>${esc(x)}</option>`).join('')};
opt('s',D.map(a=>a.src),'Toutes');opt('g',D.flatMap(a=>a.grd),'Tous');
$('th').innerHTML=COLS.map(([k,l])=>`<th data-k="${k}">${l} <i id="a-${k}"></i></th>`).join('');
const val=(a,k)=>Array.isArray(a[k])?a[k].join(', '):a[k];
function ok(a){
 const hay=nm([a.titre,a.client,a.grd.join(' '),a.segments.join(' ')].join(' ')),q=$('q').value.trim();
 if(q&&!q.split(',').some(g=>g.trim()&&nm(g).split(/\s+/).filter(Boolean).every(w=>hay.includes(w))))return false;
 if($('s').value&&a.src!==$('s').value)return false;
 if($('g').value&&!a.grd.includes($('g').value))return false;
 if($('c').value&&!nm(a.client).includes(nm($('c').value)))return false;
 const[pf,pt,lf,lt]=['pf','pt','lf','lt'].map(i=>$(i).value);
 if(pf&&a.date<pf||pt&&a.date>pt)return false;
 if((lf||lt)&&!a.limite)return false;
 if(lf&&a.limite<lf||lt&&a.limite>lt)return false;
 if($('h').checked&&a.limite&&a.limite<T)return false;
 return true}
function draw(){
 const L=D.filter(ok).sort((a,b)=>{const x=val(a,sk),y=val(b,sk);if(x==null||x==='')return 1;if(y==null||y==='')return -1;return(x>y?1:x<y?-1:0)*sd});
 $('tb').innerHTML=L.map(a=>{const n=a.limite?left(a.limite):null,p=n==null?'':`<small class="p ${n<0?'':n<=7?'hot':''}">${n<0?'clôturé':'J-'+n}</small>`;
  return`<tr><td>${fr(a.date)}</td><td>${fr(a.limite)}${p}</td><td><span class="b ${a.src}">${a.src}</span></td><td class="t"><a href="${esc(a.lien)}" target="_blank" rel="noopener">${esc(a.titre)}</a></td><td>${esc(a.client)||'—'}</td><td>${esc(a.grd.join(', '))||'—'}</td><td class="n">${a.sites??'—'}</td><td>${esc(a.segments.join(', '))||'—'}</td><td class="n">${esc(a.volume)||'—'}</td></tr>`}).join('');
 $('k1').textContent=L.length;$('k2').textContent=L.filter(a=>!a.limite||a.limite>=T).length;
 $('k3').textContent=L.filter(a=>a.limite&&left(a.limite)>=0&&left(a.limite)<=7).length;$('k4').textContent=L.filter(a=>a.grd.length).length;
 COLS.forEach(([k])=>$('a-'+k).textContent=k===sk?(sd>0?'▲':'▼'):'')}
document.querySelectorAll('input,select').forEach(e=>e.oninput=e.onchange=draw);
$('th').onclick=e=>{const k=e.target.closest('th')?.dataset.k;if(!k)return;sd=k===sk?-sd:1;sk=k;draw()};
$('r').onclick=()=>{document.querySelectorAll('.f input:not([type=checkbox]),.f select').forEach(e=>e.value='');$('h').checked=true;draw()};
draw();</script></html>"""


def rendu(items, cfg):
    d = json.dumps(items, ensure_ascii=False).replace("</", "<\\/")
    c = json.dumps({"mots_cles": cfg["mots_cles"], "maj": date.today().strftime("%d/%m/%Y")}, ensure_ascii=False)
    return PAGE.replace("%%DATA%%", d).replace("%%CFG%%", c)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--jours", type=int)
    a = ap.parse_args()
    cfg = charger_config()
    if a.jours:
        cfg["jours"] = a.jours
    items = []
    for nom in cfg["sources"]:
        try:
            r = SOURCES[nom](cfg)
            print(f"{nom.upper()} : {len(r)} avis")
            items += r
        except Exception as e:
            print(f"{nom.upper()} : échec ({e})")
    items.sort(key=lambda x: x["date"], reverse=True)
    json.dump(items, open("ao.json", "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    open("veille_ao.html", "w", encoding="utf-8").write(rendu(items, cfg))
    print("-> veille_ao.html / ao.json")
