# -*- coding: utf-8 -*-
"""
wzpn_rank.py — generator danych rankingu IV ligi wielkopolskiej (WZPN).

Jak premium: czyta secrets.toml (bez .env), tunel SSH odpalasz RĘCZNIE.
    python wzpn_rank.py                 # miesiąc = sierpień 2026 (domyślnie)
    python wzpn_rank.py --od 2026-09-01 --do 2026-09-30

DWA WIDOKI (jak ustaliliśmy):
  • SCORE / FORMA: AVG(score) ×100 z miesiąca  (= Wasz ranking.sql, zgodne z Excelem)
    + liczba meczów (apka filtruje min. mecze; domyślnie 2).
  • PROGRES: overall_score teraz − overall_score (stan) na ostatni mecz sezonu 25/26.
Dodatki: odznaka „grał w…" (najwyższy szczebel w historii), czyste nazwy klubów,
bramkarze osobno (flaga), link playmaker (opcjonalnie, PM_DB_* w secrets).

Wyjście: data/ranking/ranking_data.json (+ ranking_latest.csv).
"""
import argparse
import base64
import csv
import glob
import json
import os
import sys
from datetime import date, datetime

try:
    import psycopg2
except ImportError:
    sys.exit("Brak psycopg2. Zainstaluj: pip install psycopg2-binary")

HERE = os.path.dirname(os.path.abspath(__file__))

SEASON_2627 = "3c77d143-8010-4073-9842-d6b63365ffce"
SEASON_2526 = "e9d66181-d03e-4bb3-b889-4da648f4831d"
IV_LIGA_IDS = ["c164ca31-22e4-43fc-9e30-4f3bcc2b7d72"]
REGION_IDS = ["fd118a32-2558-437c-a1d6-76a1f862e13d"]
MIESIACE = ["", "stycznia", "lutego", "marca", "kwietnia", "maja", "czerwca",
            "lipca", "sierpnia", "września", "października", "listopada", "grudnia"]


# ── secrets.toml (jak ranking_lnp.py + sąsiednie foldery) ──
_CONF = None
def _read_conf():
    global _CONF
    if _CONF is not None:
        return _CONF
    _CONF = {}
    parent = os.path.dirname(HERE)
    cands = []
    for d in (HERE, os.getcwd(), parent):
        cands += [os.path.join(d, "secrets.toml"), os.path.join(d, ".streamlit", "secrets.toml")]
    cands += glob.glob(os.path.join(parent, "*", "secrets.toml"))
    for p in cands:
        if os.path.exists(p):
            try:
                for line in open(p, encoding="utf-8-sig"):
                    line = line.strip()
                    if not line or line.startswith("#") or line.startswith("[") or "=" not in line:
                        continue
                    k, v = line.split("=", 1)
                    _CONF.setdefault(k.strip(), v.strip().strip('"').strip("'"))
            except Exception:
                pass
            if _CONF:
                print(f"  secrets: {p}")
                break
    return _CONF


def _cfg(key, default=None, required=False, secret=False):
    v = os.environ.get(key) or _read_conf().get(key)
    if v:
        return str(v)
    if required:
        import getpass
        return getpass.getpass(f"{key}: ") if secret else input(f"{key}: ").strip()
    return default


def connect(dbname, user, password):
    return psycopg2.connect(host=_cfg("PGHOST", "localhost"), port=int(_cfg("PGPORT", "5433")),
                            dbname=dbname, user=user, password=password,
                            client_encoding="UTF8", connect_timeout=30)


def _inlist(ids):
    return ",".join("'" + str(x).replace("'", "") + "'" for x in ids) if ids else "NULL"


def s100(v):
    try: return round(float(v) * 100, 1)
    except Exception: return None


# ── SCORE / FORMA (miesiąc, AVG score ×100 — jak ranking.sql) ──
def ranking_miesiac(lnp, od, do):
    reg = (f"AND s.play_id IN (SELECT _id FROM plays WHERE region_id IN ({_inlist(REGION_IDS)}))"
           if REGION_IDS else "")
    sql = f"""
      WITH agg AS (
        SELECT s.player_id,
               bool_or(m.is_keeper) AS keeper,
               count(*)             AS mecze,
               avg(s.score)         AS avg_score,
               max(s.team_id::text) AS team_id
        FROM pm_player_match_score s
        JOIN pm_player_match_stats m ON s.match_id=m.match_id AND s.player_id=m.player_id
        WHERE s.season_id = %s
          AND s.league_id IN ({_inlist(IV_LIGA_IDS)})
          {reg}
          AND s.match_date::date BETWEEN %s AND %s
          AND m.minutes > 0
          AND s.score IS NOT NULL AND s.score <> 'NaN'::double precision
        GROUP BY s.player_id
      )
      SELECT agg.player_id::text, agg.keeper, agg.mecze, agg.avg_score, agg.team_id,
             a.firstname, a.lastname, LEFT(a.date_of_birth,4) AS yob,
             b.name AS team_name, b.club_id::text AS club_id, cl.name AS club_name
      FROM agg
      JOIN players a ON agg.player_id = a._id
      LEFT JOIN teams b ON agg.team_id = b._id
      LEFT JOIN clubs cl ON b.club_id = cl._id
    """
    cur = lnp.cursor(); cur.execute(sql, (SEASON_2627, od, do)); rows = cur.fetchall(); cur.close()
    return rows


def overall_teraz(lnp, ids):
    """{pid: overall_score} z ostatniego meczu sezonu 26/27 (wartość „teraz" do progresu)."""
    return _last_overall(lnp, ids, SEASON_2627)


def baza_2526(lnp, ids, od="2025-07-01", do="2026-07-01"):
    """{pid: overall_score} stan na OSTATNI mecz przed sezonem 26/27 (okno 25/26, po dacie)."""
    out = {}
    if not ids: return out
    cur = lnp.cursor(); B = 800
    for i in range(0, len(ids), B):
        chunk = _inlist(ids[i:i + B])
        cur.execute(f"""
          SELECT DISTINCT ON (player_id) player_id::text, overall_score
          FROM pm_player_match_score
          WHERE player_id::text IN ({chunk})
            AND match_date::date >= %s AND match_date::date < %s
            AND overall_score IS NOT NULL
          ORDER BY player_id, match_date DESC NULLS LAST
        """, (od, do))
        for pid, ov in cur.fetchall(): out[pid] = ov
    cur.close(); return out


def _last_overall(lnp, ids, season):
    out = {}
    if not ids: return out
    cur = lnp.cursor(); B = 800
    for i in range(0, len(ids), B):
        chunk = _inlist(ids[i:i + B])
        cur.execute(f"""
          SELECT DISTINCT ON (player_id) player_id::text, overall_score
          FROM pm_player_match_score
          WHERE season_id = %s AND player_id::text IN ({chunk}) AND overall_score IS NOT NULL
          ORDER BY player_id, match_date DESC NULLS LAST
        """, (season,))
        for pid, ov in cur.fetchall(): out[pid] = ov
    cur.close(); return out


def tier_leagues(lnp):
    cur = lnp.cursor(); cur.execute("SELECT _id, name FROM leagues"); rows = cur.fetchall(); cur.close()
    out = {}
    for _id, name in rows:
        n = (name or "").lower()
        if "kobiet" in n or "juniorek" in n or "futsal" in n: continue
        if "ekstraklasa" in n and "młoda" not in n and "mloda" not in n: out[_id] = (1, name)
        elif "młoda ekstraklasa" in n or "mloda ekstraklasa" in n: out[_id] = (2, name)
        elif n.strip() in ("pierwsza liga", "i liga"): out[_id] = (3, name)
        elif n.strip() in ("druga liga", "ii liga"): out[_id] = (4, name)
        elif n.startswith("clj") or "centralna liga junior" in n: out[_id] = (5, name)
    return out


def odznaki(lnp, ids, tiers):
    out = {}
    if not ids or not tiers: return out
    tin = _inlist(list(tiers.keys())); best = {}
    cur = lnp.cursor(); B = 800
    for i in range(0, len(ids), B):
        chunk = _inlist(ids[i:i + B])
        cur.execute(f"""
          SELECT DISTINCT s.player_id::text, s.league_id::text
          FROM pm_player_match_score s
          JOIN pm_player_match_stats m ON s.match_id=m.match_id AND s.player_id=m.player_id
          WHERE s.player_id::text IN ({chunk}) AND m.minutes>0 AND s.league_id IN ({tin})
        """)
        for pid, lid in cur.fetchall():
            pr, nm = tiers.get(lid, (99, None))
            if nm and (pid not in best or pr < best[pid][0]): best[pid] = (pr, nm)
    cur.close()
    return {pid: nm for pid, (pr, nm) in best.items()}


def linki_playmaker(ids):
    pm_db, pm_u, pm_p = _cfg("PM_DB_NAME"), _cfg("PM_DB_USER"), _cfg("PM_DB_PASSWORD")
    if not (pm_db and pm_u and pm_p):
        print("  linki playmaker: pominięte (brak PM_DB_* w secrets.toml)"); return {}
    try:
        pm = connect(pm_db, pm_u, pm_p)
    except Exception as e:
        print(f"  linki playmaker: nie połączono ({e})"); return {}
    out = {}; cur = pm.cursor(); B = 800
    try:
        for i in range(0, len(ids), B):
            chunk = _inlist(ids[i:i + B])
            cur.execute(f"""
              SELECT DISTINCT ON (me.mapper_id) me.mapper_id::text, pp.slug
              FROM mapper_mapperentity me JOIN profiles_playerprofile pp ON pp.mapper_id = me.target_id
              WHERE me.mapper_id::text IN ({chunk})
                AND me.related_type='player' AND me.database_source='new_scrapper_mongodb' AND pp.slug IS NOT NULL
              ORDER BY me.mapper_id, pp.verification_stage_id DESC NULLS LAST
            """)
            for uuid, slug in cur.fetchall():
                if slug: out[uuid] = "playmaker.pro/profil/" + slug
    except Exception as e:
        print(f"  linki playmaker: błąd ({e})")
    finally:
        cur.close(); pm.close()
    return out


def load_maps():
    parent = os.path.dirname(HERE); cands = []
    for d in (HERE, os.getcwd(), parent):
        cands += glob.glob(os.path.join(d, "*teamy_kluby*.csv"))
    cands += glob.glob(os.path.join(parent, "*", "*teamy_kluby*.csv"))
    path = cands[0] if cands else None
    tm, cm = {}, {}
    if not path or not os.path.exists(path):
        print("  mapping nazw: brak *teamy_kluby*.csv (surowe)"); return tm, cm
    with open(path, encoding="utf-8-sig", newline="") as f:
        rd = csv.DictReader(f); cols = {c.lower(): c for c in (rd.fieldnames or [])}
        c_tid, c_cid = cols.get("team_id"), cols.get("club_id")
        c_tn = cols.get("final_team_name") or cols.get("final_name")
        c_cn = cols.get("final_club_name") or cols.get("club_name")
        for r in rd:
            tid = (r.get(c_tid) or "").strip() if c_tid else ""
            cid = (r.get(c_cid) or "").strip() if c_cid else ""
            tn = (r.get(c_tn) or "").strip() if c_tn else ""
            cn = (r.get(c_cn) or "").strip() if c_cn else ""
            if tid and tn: tm[tid] = tn
            if cid and cn and cid not in cm: cm[cid] = cn
    print(f"  mapping nazw: {os.path.basename(path)} (team={len(tm)}, club={len(cm)})")
    return tm, cm


def _logo():
    for name in ("wzpn.jpeg", "wzpn.jpg", "wzpn.png", "logo.png"):
        p = os.path.join(HERE, name)
        if os.path.exists(p):
            mime = "image/png" if name.endswith(".png") else "image/jpeg"
            return f"data:{mime};base64," + base64.b64encode(open(p, "rb").read()).decode()
    return None


TEMPLATE = r'''<!DOCTYPE html>
<html lang="pl"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<title>IV liga wielkopolska — ranking</title>
<style>
  :root{--bg:#0a0c10;--card:#12151c;--line:#20242e;--ink:#fff;--mut:#8a90a0;--blue:#3b74d6;--grn:#31C56A;--red:#F0603C;--gold:#E0A93C;}
  *{box-sizing:border-box;-webkit-tap-highlight-color:transparent}
  body{margin:0;background:var(--bg);color:var(--ink);font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Inter,Arial,sans-serif;font-size:16px;line-height:1.35}
  .wrap{max-width:560px;margin:0 auto;padding:16px 14px 48px}
  a{color:inherit;text-decoration:none}
  .hd{display:flex;align-items:center;gap:12px;padding:4px 2px 14px;border-bottom:1px solid var(--line)}
  .hd .logo{background:#fff;border-radius:12px;padding:7px;display:flex}
  .hd .logo img{height:46px;width:auto;display:block}
  .hd .t{font-weight:800;font-size:17px;line-height:1.1}
  .hd .d{color:var(--mut);font-size:12px;margin-top:2px}
  .filters{position:sticky;top:0;z-index:5;background:linear-gradient(var(--bg),var(--bg) 80%,rgba(10,12,16,0));padding:12px 2px 8px}
  .flabel{font-size:11px;color:var(--mut);text-transform:uppercase;letter-spacing:.06em;margin:8px 2px 4px}
  .frow{display:flex;gap:8px;flex-wrap:wrap;padding-bottom:4px}
  .seg{display:inline-flex;background:var(--card);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px}
  .seg button{border:0;background:transparent;color:var(--mut);font-size:13px;font-weight:700;padding:7px 12px;border-radius:8px;cursor:pointer;white-space:nowrap}
  .seg button.on{background:var(--blue);color:#fff}
  select{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:9px 12px;font-size:14px;font-weight:600;min-width:130px}
  input.search{background:var(--card);color:var(--ink);border:1px solid var(--line);border-radius:10px;padding:9px 12px;font-size:14px;width:100%;margin-top:8px}
  .exp{margin-top:8px;width:100%;padding:10px;background:var(--card);border:1px solid var(--line);border-radius:10px;color:var(--ink);font-weight:700;font-size:13px;cursor:pointer}
  .exp:hover{border-color:var(--blue)}
  .sec{font-size:12px;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;margin:20px 2px 10px;font-weight:700}
  .gain{display:flex;align-items:center;gap:12px;background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px;margin-bottom:10px}
  .gain.top{border-color:#33507f;background:linear-gradient(180deg,#141a26,#10131a)}
  .gain .pos{font-weight:900;font-size:20px;color:var(--mut);min-width:26px;text-align:center}.gain.top .pos{color:var(--gold)}
  .gain .info{flex:1;min-width:0}
  .gain .nm{font-weight:800;font-size:17px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .gain .cl{color:var(--mut);font-size:13px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .gain .chg{text-align:right;flex-shrink:0}.gain .big{font-weight:900;font-size:22px;line-height:1}
  .gain .sub{color:var(--mut);font-size:12px;margin-top:3px;font-weight:600}
  .badge{display:inline-block;font-size:11px;font-weight:800;color:#111;background:var(--gold);padding:1px 7px;border-radius:6px;margin-top:4px}
  .chev{color:var(--mut);font-size:18px;flex-shrink:0}
  .row{display:flex;align-items:center;gap:11px;padding:11px 8px;border-bottom:1px solid var(--line)}
  .row.me{background:rgba(59,116,214,.12);border-radius:10px}
  .row .pos{min-width:30px;text-align:center;font-weight:800;color:var(--mut);font-size:14px}
  .row .info{flex:1;min-width:0}
  .row .nm{font-weight:700;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .row .cl{color:var(--mut);font-size:12px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .row .badge{font-size:10px;margin:0 0 0 6px;padding:1px 5px;vertical-align:1px}
  .row .v{font-weight:800;font-size:16px;min-width:54px;text-align:right}
  .row .m{min-width:34px;text-align:right;color:var(--mut);font-size:12px;font-weight:600}
  .up{color:var(--grn)}.dn{color:var(--red)}
  .more{width:100%;margin-top:12px;padding:12px;background:var(--card);border:1px solid var(--line);border-radius:12px;color:var(--ink);font-weight:700;font-size:14px;cursor:pointer}
  .empty{color:var(--mut);text-align:center;padding:24px;font-size:14px}
  .count{color:var(--mut);font-size:12px;margin:2px 2px 8px}
  .foot{margin-top:26px;padding-top:16px;border-top:1px solid var(--line);display:flex;justify-content:space-between;align-items:center;color:var(--mut);font-size:12px}
</style></head><body><div class="wrap">
  <div class="hd">__BRAND__<div><div class="t">IV liga wielkopolska</div><div class="d">Ranking zawodników · __OKRES__</div></div></div>
  <div class="filters">
    <div class="flabel">Widok / pozycja</div>
    <div class="frow">
      <div class="seg" id="segMode"><button data-v="forma" class="on">Forma</button><button data-v="progres">Progres</button></div>
      <div class="seg" id="segPos"><button data-v="pol" class="on">z pola</button><button data-v="br">Bramkarze</button></div>
    </div>
    <div class="flabel">Filtry</div>
    <div class="frow">
      <div class="seg" id="segMin"><button data-v="1">≥1</button><button data-v="2" class="on">≥2 mecze</button><button data-v="3">≥3</button><button data-v="5">≥5</button></div>
      <select id="fKlub"><option value="all">Wszystkie kluby</option></select>
      <select id="fRok"><option value="all">Rocznik: wszystkie</option></select>
    </div>
    <input class="search" id="q" placeholder="Szukaj zawodnika…">
    <button class="exp" id="expBtn">⬇ Eksport do Excela (bieżący widok)</button>
  </div>
  <div class="sec" id="topTitle">TOP forma miesiąca</div>
  <div id="gains"></div>
  <div class="sec" id="rankTitle">Ranking</div>
  <div class="count" id="rankCount"></div>
  <div id="rank"></div>
  <button class="more" id="moreBtn" style="display:none"></button>
  <div class="foot"><div>IV liga wielkopolska · sezon 2026/27</div><div>__FOOTER__</div></div>
</div>
<script src="https://cdnjs.cloudflare.com/ajax/libs/xlsx/0.18.5/xlsx.full.min.js"></script>
<script>
const PLAYERS=__DATA__, CAP=120;
let mode="forma", fKeeper=false, fMin=2, fKlub="all", fRok="all", q="", shown=CAP;
const MEQ=new URLSearchParams(location.search).get("me"); const MEI="__ME__"; const ME=MEQ||((MEI&&MEI.slice(0,2)!=="__")?MEI:null);
const val=p=> mode==="progres"? p.progres : p.score;
const f1=x=>x==null?"—":x.toFixed(1).replace(".",","); const fj=x=>x==null?"—":(x>0?"+":"")+x.toFixed(1).replace(".",",");
const esc=s=>(s||"").replace(/[&<>"]/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));
const href=u=>u?(u.startsWith("http")?u:"https://"+u):null;
function passes(p){
  if(p.keeper!==fKeeper) return false;
  if((p.mecze||0)<fMin) return false;
  if(fKlub!=="all" && p.club!==fKlub) return false;
  if(fRok!=="all" && String(p.yob)!==fRok) return false;
  if(mode==="progres" && p.progres==null) return false;
  if(q && !(p.name||"").toLowerCase().includes(q)) return false;
  return true;
}
function nm(p){const u=href(p.url);const i=esc(p.name||"—");return u?`<a href="${u}" target="_blank" rel="noopener">${i}</a>`:i;}
function bdg(p){return p.badge?`<span class="badge">grał: ${esc(p.badge)}</span>`:"";}
function go(u){if(u)window.open(u,"_blank","noopener");}
function renderGains(list){
  const el=document.getElementById("gains"),ttl=document.getElementById("topTitle");
  ttl.textContent = mode==="progres" ? "TOP progres (vs koniec 25/26)" : "TOP forma miesiąca";
  const g=list.slice().sort((a,b)=>(val(b)??-999)-(val(a)??-999)).slice(0,5);
  if(!g.length){el.innerHTML='<div class="empty">Brak zawodników dla tych filtrów.</div>';return;}
  el.innerHTML=g.map((p,i)=>{
    const big = mode==="progres" ? `<div class="big ${p.progres>=0?'up':'dn'}">${fj(p.progres)}</div>` : `<div class="big">${f1(p.score)}</div>`;
    const sub = mode==="progres" ? `${f1(p.ov_base)} → ${f1(p.ov_now)}` : `${p.mecze} mecze`;
    return `<div class="gain ${i===0?'top':''}" onclick="go('${href(p.url)||''}')">
      <div class="pos">${i+1}</div>
      <div class="info"><div class="nm">${nm(p)}</div><div class="cl">${esc(p.club||'')}${p.team&&p.team!==p.club?' · '+esc(p.team):''}</div>${bdg(p)}</div>
      <div class="chg">${big}<div class="sub">${sub}</div></div>${href(p.url)?'<div class="chev">›</div>':''}</div>`;}).join("");
}
function renderRank(list){
  const arr=list.slice().sort((a,b)=>(val(b)??-999)-(val(a)??-999));
  document.getElementById("rankTitle").textContent = mode==="progres" ? "Ranking progresu" : "Ranking formy";
  document.getElementById("rankCount").textContent = arr.length+" zawodników"+(arr.length>shown?(" · pokazano "+shown):"");
  const el=document.getElementById("rank");
  if(!arr.length){el.innerHTML='<div class="empty">Brak wyników.</div>';document.getElementById("moreBtn").style.display="none";return;}
  el.innerHTML=arr.slice(0,shown).map((p,i)=>{
    const me=(ME&&p.id===ME)?"me":"";
    const v = mode==="progres" ? `<div class="v ${p.progres>=0?'up':'dn'}">${fj(p.progres)}</div>` : `<div class="v">${f1(p.score)}</div>`;
    return `<div class="row ${me}" onclick="go('${href(p.url)||''}')"><div class="pos">${i+1}</div>
      <div class="info"><div class="nm">${nm(p)}${bdg(p)}</div><div class="cl">${esc(p.club||'')}${p.team&&p.team!==p.club?' · '+esc(p.team):''}</div></div>
      ${v}<div class="m">${p.mecze||0}m</div></div>`;}).join("");
  const mb=document.getElementById("moreBtn");
  if(arr.length>shown){mb.style.display="block";mb.textContent="Pokaż więcej ("+(arr.length-shown)+")";mb.onclick=()=>{shown+=CAP;renderRank(list);};}
  else mb.style.display="none";
  if(ME){const r=el.querySelector(".me");if(r)r.scrollIntoView({block:"center"});}
}
function render(){shown=Math.max(shown,CAP);const list=PLAYERS.filter(passes);renderGains(list);renderRank(list);}
function fillSelect(id,vals,label){const s=document.getElementById(id);vals.forEach(v=>{const o=document.createElement("option");o.value=v;o.textContent=v;s.appendChild(o);});}
function initFilters(){
  const kluby=[...new Set(PLAYERS.map(p=>p.club).filter(Boolean))].sort((a,b)=>a.localeCompare(b,"pl"));
  fillSelect("fKlub",kluby);
  const roczniki=[...new Set(PLAYERS.map(p=>p.yob).filter(Boolean))].sort((a,b)=>b-a).map(String);
  fillSelect("fRok",roczniki);
  document.getElementById("fKlub").onchange=e=>{fKlub=e.target.value;shown=CAP;render();};
  document.getElementById("fRok").onchange=e=>{fRok=e.target.value;shown=CAP;render();};
  document.getElementById("q").oninput=e=>{q=e.target.value.toLowerCase().trim();shown=CAP;render();};
  const bind=(id,fn)=>document.querySelectorAll("#"+id+" button").forEach(b=>b.onclick=()=>{document.querySelectorAll("#"+id+" button").forEach(x=>x.classList.remove("on"));b.classList.add("on");fn(b.dataset.v);shown=CAP;render();});
  bind("segMode",v=>mode=v); bind("segPos",v=>fKeeper=(v==="br")); bind("segMin",v=>fMin=parseInt(v));
  document.getElementById("expBtn").onclick=exportExcel;
}
function exportExcel(){
  if(typeof XLSX==="undefined"){alert("Eksport chwilowo niedostępny (brak biblioteki).");return;}
  const arr=PLAYERS.filter(passes).sort((a,b)=>(val(b)??-999)-(val(a)??-999));
  const rows=arr.map((p,i)=>({
    "#": i+1, "Zawodnik": p.name||"", "Klub": p.club||"", "Drużyna": p.team||"",
    "Rocznik": p.yob||"", "Pozycja": p.keeper?"bramkarz":"z pola", "Mecze": p.mecze||0,
    "Śr. ocena (forma)": p.score, "Progres": p.progres,
    "Overall teraz": p.ov_now, "Overall 25/26": p.ov_base, "Grał w": p.badge||""
  }));
  const ws=XLSX.utils.json_to_sheet(rows);
  const wb=XLSX.utils.book_new();
  XLSX.utils.book_append_sheet(wb, ws, (mode==="progres"?"Progres":"Forma")+(fKeeper?" - bramkarze":" - z pola"));
  XLSX.writeFile(wb, "ranking_IV_liga_wlkp.xlsx");
}
initFilters();render();
</script></body></html>'''


def build_html(players, okres, logo=None, footer="playmaker.pro", me=""):
    brand = (f'<div class="logo"><img src="{logo}" alt="WZPN"></div>' if logo else '<div class="t">WZPN</div>')
    return (TEMPLATE.replace("__BRAND__", brand).replace("__OKRES__", okres or "")
            .replace("__FOOTER__", footer).replace("__ME__", me or "")
            .replace("__DATA__", json.dumps(players, ensure_ascii=False)))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--od", default="2026-08-01", help="początek miesiąca (RRRR-MM-DD)")
    ap.add_argument("--do", dest="do_", default="2026-08-31", help="koniec miesiąca (RRRR-MM-DD)")
    ap.add_argument("--outdir", default=os.path.join(HERE, "data", "ranking"))
    a = ap.parse_args()
    os.makedirs(a.outdir, exist_ok=True)

    print("[LNP] łączę (tunel SSH musi być OTWARTY w osobnym oknie) ...")
    lnp = connect(_cfg("PGDATABASE", required=True), _cfg("PGUSER", required=True), _cfg("PGPASSWORD", required=True, secret=True))
    print("  OK")
    rows = ranking_miesiac(lnp, a.od, a.do_)
    print(f"  IV liga wlkp {a.od}..{a.do_}: {len(rows)} zawodników (z meczem w oknie)")
    ids = [r[0] for r in rows]
    now = overall_teraz(lnp, ids)
    base = baza_2526(lnp, ids)
    print(f"  overall teraz: {len(now)} | baza 25/26: {len(base)}")
    tiers = tier_leagues(lnp)
    odz = odznaki(lnp, ids, tiers)
    print(f"  szczeble: {len(tiers)} | odznaki: {len(odz)}")
    links = linki_playmaker(ids)
    print(f"  linki playmaker: {len(links)}")
    lnp.close()
    tm, cm = load_maps()

    players = []
    for r in rows:
        (pid, keeper, mecze, avg_score, team_id, fn, ln, yob, team_name, club_id, club_name) = r
        avg100 = s100(avg_score)
        ov_now = s100(now.get(pid)); ov_base = s100(base.get(pid))
        prog = round(ov_now - ov_base, 1) if (ov_now is not None and ov_base is not None) else None
        klub = (cm.get(club_id) if club_id else None) or club_name or ""
        druzyna = (tm.get(team_id) if team_id else None) or team_name or ""
        players.append({
            "id": pid, "name": ((fn or "") + " " + (ln or "")).strip() or "Zawodnik",
            "club": klub, "team": druzyna,
            "yob": (int(yob) if (yob and str(yob).isdigit()) else None),
            "keeper": bool(keeper), "mecze": int(mecze) if mecze is not None else 0,
            "score": avg100,                 # FORMA miesiąca (AVG, ×100) = jak Excel
            "ov_now": ov_now, "ov_base": ov_base, "progres": prog,   # PROGRES (overall)
            "badge": odz.get(pid), "url": links.get(pid),
        })
    players.sort(key=lambda p: (p["score"] if p["score"] is not None else -1), reverse=True)

    try:
        m = datetime.strptime(a.od, "%Y-%m-%d"); okres = f"{MIESIACE[m.month]} {m.year}"
    except Exception:
        okres = f"{a.od}..{a.do_}"
    data = {"players": players, "sezon": "2026/27", "okres": okres, "od": a.od, "do": a.do_,
            "wygenerowano": date.today().strftime("%Y-%m-%d"), "logo": _logo()}
    outp = os.path.join(a.outdir, "ranking_data.json")
    with open(outp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    with open(os.path.join(a.outdir, "ranking.html"), "w", encoding="utf-8") as f:
        f.write(build_html(players, okres, logo=data["logo"]))

    with open(os.path.join(a.outdir, "ranking_latest.csv"), "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["imie_nazwisko", "klub", "druzyna", "rocznik", "bramkarz", "mecze",
                    "score_forma", "overall_teraz", "overall_2526", "progres", "odznaka", "url"])
        for p in players:
            w.writerow([p["name"], p["club"], p["team"], p["yob"] or "", "TAK" if p["keeper"] else "", p["mecze"],
                        p["score"], p["ov_now"] if p["ov_now"] is not None else "",
                        p["ov_base"] if p["ov_base"] is not None else "",
                        p["progres"] if p["progres"] is not None else "", p["badge"] or "", p["url"] or ""])

    polowi = sum(1 for p in players if not p["keeper"])
    print(f"\n  RAZEM: {len(players)} (polowi {polowi}, bramkarze {len(players)-polowi}) | "
          f"z progresem {sum(1 for p in players if p['progres'] is not None)} | z odznaką {sum(1 for p in players if p['badge'])}")
    print(f"  okres: {okres}")
    print(f"✓ JSON: {outp}")


if __name__ == "__main__":
    main()
