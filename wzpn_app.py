# -*- coding: utf-8 -*-
"""
wzpn_app.py - serwuje ranking IV ligi wielkopolskiej.

Trzy zakresy do przelaczania (gdy odpowiednie JSON-y istnieja):
  * glowny miesiac  -> data/ranking/ranking_data.json
  * drugi miesiac   -> data/ranking/ranking_prev.json
  * sezon total     -> data/ranking/ranking_sezon.json

Generowanie danych (jedna komenda = trzy pliki):
    python wzpn_rank.py --od 2026-10-01 --do 2026-10-31 \
                       --od2 2026-09-01 --do2 2026-09-30

URUCHOMIENIE:
    streamlit run wzpn_app.py

Link zawodnika: <adres-apki>/?me=<player_id UUID>
"""
import json
import os
import streamlit as st
import streamlit.components.v1 as components

from wzpn_rank import build_html

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_MAIN  = os.path.join(HERE, "data", "ranking", "ranking_data.json")
DATA_PREV  = os.path.join(HERE, "data", "ranking", "ranking_prev.json")
DATA_SEZON = os.path.join(HERE, "data", "ranking", "ranking_sezon.json")

st.set_page_config(page_title="IV liga wielkopolska - ranking", page_icon="⚽",
                   layout="centered", initial_sidebar_state="collapsed")
st.markdown("""<style>
  #MainMenu, header, footer {visibility:hidden;}
  .block-container {padding:0 !important; max-width:600px;}
  .stApp {background:#0a0c10;}
  div[data-testid="stSegmentedControl"] {padding:10px 14px 0;}
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=1800)
def load(path, mtime):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# zbierz dostepne zakresy w kolejnosci: glowny miesiac -> drugi miesiac -> sezon
dostepne = {}
if os.path.exists(DATA_MAIN):
    d = load(DATA_MAIN, os.path.getmtime(DATA_MAIN))
    label = (d.get("okres") or "Miesiac").capitalize()
    dostepne[label] = d
if os.path.exists(DATA_PREV):
    d = load(DATA_PREV, os.path.getmtime(DATA_PREV))
    label = (d.get("okres") or "Poprzedni").capitalize()
    dostepne[label] = d
if os.path.exists(DATA_SEZON):
    d = load(DATA_SEZON, os.path.getmtime(DATA_SEZON))
    label = "Sezon " + d.get("sezon", "")
    dostepne[label] = d

if not dostepne:
    st.error("Brak danych. Najpierw wygeneruj: `python wzpn_rank.py` (tunel SSH otwarty).")
    st.stop()

opcje = list(dostepne.keys())
if len(opcje) > 1:
    wybor = st.segmented_control("Zakres", opcje, default=opcje[0],
                                 label_visibility="collapsed")
    if wybor is None:
        wybor = opcje[0]
else:
    wybor = opcje[0]

d = dostepne[wybor]
me = st.query_params.get("me", "")
html = build_html(d["players"], d.get("okres", ""), logo=d.get("logo"),
                  footer="playmaker.pro", me=me)
components.html(html, height=2200, scrolling=True)
