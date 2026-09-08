# -*- coding: utf-8 -*-
"""
wzpn_app.py — serwuje ranking IV ligi wielkopolskiej z gotowego ranking_data.json.

NIE łączy się z bazą. Dane generuje raz w miesiącu:
    python wzpn_rank.py            (z otwartym tunelem SSH)

URUCHOMIENIE:
    pip install streamlit
    streamlit run wzpn_app.py

Link zawodnika: <adres-apki>/?me=<player_id UUID>
Eksport do Excela jest w samej apce (jeden przycisk, respektuje filtry).
"""
import json
import os
import streamlit as st
import streamlit.components.v1 as components

from wzpn_rank import build_html

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data", "ranking", "ranking_data.json")

st.set_page_config(page_title="IV liga wielkopolska — ranking", page_icon="⚽",
                   layout="centered", initial_sidebar_state="collapsed")
st.markdown("""<style>
  #MainMenu, header, footer {visibility:hidden;}
  .block-container {padding:0 !important; max-width:600px;}
  .stApp {background:#0a0c10;}
</style>""", unsafe_allow_html=True)


@st.cache_data(ttl=1800)
def load(path, mtime):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


if not os.path.exists(DATA):
    st.error("Brak danych. Najpierw wygeneruj: `python wzpn_rank.py` (tunel SSH otwarty).")
    st.stop()

d = load(DATA, os.path.getmtime(DATA))
me = st.query_params.get("me", "")
html = build_html(d["players"], d.get("okres", ""), logo=d.get("logo"),
                  footer="playmaker.pro", me=me)
components.html(html, height=2200, scrolling=True)
