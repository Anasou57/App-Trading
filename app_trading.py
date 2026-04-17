import streamlit as st
import ccxt
import pandas as pd
import pandas_ta as ta
import json
import os
from datetime import datetime

# --- PERSISTANCE DES DONNÉES ---
DB_FILE = "trading_journal.json"
HIST_FILE = "trading_history.json"

def load_data(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:
                data = json.load(f)
                return data
            except:
                return [] if "history" in file else {}
    return [] if "history" in file else {}

def save_data(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

# --- ARCHIVAGE DÉTAILLÉ ---
def archive_position(symbol, data, exit_price, reason):
    history = load_data(HIST_FILE)
    pnl = ((exit_price - data['entry']) / data['entry']) * 100
    
    history.append({
        "SYMBOLE": symbol,
        "OUVERTURE": data.get('time_full', data.get('time', "N/A")),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %": f"{pnl:+.2f}%",
        "RAISON": reason,
        "STYLE": data['style'],
        "ENTREE": f"{data['entry']:.4f}",
        "SORTIE": f"{exit_price:.4f}",
        "pnl": round(pnl, 2),
        "date": datetime.now().strftime("%Y-%m-%d")
    })
    save_data(HIST_FILE, history)
    return pnl

# --- CONFIGURATION INITIALE ---
st.set_page_config(page_title="SMC PERFORMANCE V7.7 (KUCOIN)", layout="wide")
if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

# REMPLACEMENT DE BINANCE PAR KUCOIN POUR STREAMLIT CLOUD
exchange = ccxt.kucoin({'timeout': 30000, 'enableRateLimit': True})

# --- MOTEUR D'ANALYSE ---
def get_market_analysis(symbol, mode_choisi):
    try:
        symbol = symbol.upper().strip()
        # KuCoin utilise aussi le format SYMBOL/USDT
        if not symbol.endswith('/USDT'):
            symbol = f"{symbol}/USDT"
            
        tf = "5m" if "SCALPING" in mode_choisi or "RANGE" in mode_choisi else "1h"
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        adx = ta.adx(df['h'], df['l'], df['c'], length=14)['ADX_14'].iloc[-1]
        bb = ta.bbands(df['c'], length=20, std=2)
        prix = df['c'].iloc[-1]
        atr = ta.atr(df['h'], df['l'], df['c'], length=14).iloc[-1]
        return {
            "symbol": symbol, "prix": prix, "adx": adx, "is_range": adx < 22, "atr": atr,
            "upper": bb['BBU_20_2.0'].iloc[-1], "lower": bb['BBL_20_2.0'].iloc[-1]
        }
    except: return None

# --- STYLE INTERFACE ---
st.markdown("""
    <style>
    .main { background-color: #000000; color: #00FF41; font-family: 'Consolas', monospace; }
    .stMetric { background-color: #0a0a0a; border: 1px solid #00FF41; padding: 15px; border-radius: 10px; }
    .stTabs [data-baseweb="tab"] { color: #00FF41; font-weight: bold; font-size: 18px; }
    .stButton>button { border: 1px solid #00FF41; background-color: #050505; color: #00FF41; width: 100%; }
    .stButton>button:hover { background-color: #00FF41; color: black; }
    </style>
    """, unsafe_allow_html=True)

# --- INTERFACE PRINCIPALE ---
st.title("📟 SMC PERFORMANCE V7.7")

# Ligne de test de connexion (Indispensable pour le Cloud)
try:
    test_p = exchange.fetch_ticker('BTC/USDT')['last']
    st.success(f"✅ Connexion KUCOIN OK | BTC: {test_p}$")
except Exception as e:
    st.error(f"❌ Erreur de connexion API : {e}")

tab_scan, tab_search, tab_journal = st.tabs(["🔎 SCANNER INTELLIGENT", "🔍 RECHERCHE PAIRE", "📈 JOURNAL & PERFORMANCE"])

with st.sidebar:
    st.header("⚙️ CONFIGURATION")
    mode_actuel = st.selectbox("CHOISIR STYLE", ["SCALPING (5m)", "RANGE MODE (Rebond)", "DAY TRADING (1h)", "SWING (4h)"])
    if st.button("🔄 ACTUALISER TOUT"): st.rerun()

# --- ONGLET 1 : SCANNER ---
with tab_scan:
    col_list, col_focus = st.columns([1, 2])
    with col_list:
        if st.button(f"LANCER SCAN {mode_actuel}"):
            with st.status("Analyse des flux KuCoin..."):
                tickers = exchange.fetch_tickers()
                # Filtrage des paires USDT sur KuCoin
                pairs = sorted([s for s in tickers if s.endswith('/USDT')], 
                              key=lambda x: tickers[x].get('quoteVolume', 0) if tickers[x].get('quoteVolume') else 0, reverse=True)[:35]
                valid_results = []
                for p in pairs:
                    ana = get_market_analysis(p, mode_actuel)
                    if ana and (("RANGE" in mode_actuel and ana['is_range']) or ("RANGE" not in mode_actuel and not ana['is_range'])):
                        valid_results.append(p)
                st.session_state['scan_res'] = valid_results
        if 'scan_res' in st.session_state:
            for p in st.session_state['scan_res']:
                if st.button(f"🎯 {p}", key=f"btn_{p}"): st.session_state['active_p'] = p

    with col_focus:
        if 'active_p' in st.session_state:
            p = st.session_state['active_p']
            res = get_market_analysis(p, mode_actuel)
            if res:
                st.header(f"💼 Analyse : {p}")
                entry = res['lower'] if "RANGE" in mode_actuel else res['prix']
                tp = res['upper'] if "RANGE" in mode_actuel else entry + (res['atr'] * 4.5)
                sl = entry - (res['atr'] * 2.0)
                tp_pct, sl_pct = ((tp - entry) / entry) * 100, ((sl - entry) / entry) * 100

                st_c1, st_c2, st_c3 = st.columns(3)
                st_c1.error(f"SL: ({sl_pct:.2f}%) {sl:,.4f}")
                st_c2.warning(f"ENTREE: {entry:,.4f}")
                st_c3.success(f"TP: (+{tp_pct:.2f}%) {tp:,.4f}")

                if st.button("🚀 VALIDER ET SURVEILLER"):
                    st.session_state['test_positions'][p] = {
                        "entry": entry, "tp": tp, "sl": sl, "tp_pct": tp_pct, "sl_pct": sl_pct,
                        "style": mode_actuel, "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

# --- ONGLET 2 : RECHERCHE MANUELLE ---
with tab_search:
    st.subheader("🔍 Analyser une paire spécifique (KuCoin)")
    pair_input = st.text_input("Ex: BTC, ETH, KCS", key="manual_search").upper().strip()
    
    if pair_input:
        analysis = get_market_analysis(pair_input, mode_actuel)
        if analysis:
            p_full = analysis['symbol']
            st.header(f"💼 Analyse : {p_full}")
            
            entry_s = analysis['lower'] if "RANGE" in mode_actuel else analysis['prix']
            tp_s = analysis['upper'] if "RANGE" in mode_actuel else entry_s + (analysis['atr'] * 4.5)
            sl_s = entry_s - (analysis['atr'] * 2.0)
            tp_p, sl_p = ((tp_s - entry_s) / entry_s) * 100, ((sl_s - entry_s) / entry_s) * 100

            m1, m2, m3 = st.columns(3)
            m1.metric("PRIX LIVE", f"{analysis['prix']:.6f}")
            m2.metric("ADX", f"{analysis['adx']:.1f}", "Range" if analysis['is_range'] else "Trend")
            m3.metric("ATR", f"{analysis['atr']:.6f}")

            st.write("---")
            sc1, sc2, sc3 = st.columns(3)
            sc1.error(f"SL: ({sl_p:.2f}%) {sl_s:,.4f}")
            sc2.warning(f"ENTRÉE: {entry_s:,.4f}")
            sc3.success(f"TP: (+{tp_p:.2f}%) {tp_s:,.4f}")

            if st.button(f"🚀 SURVEILLER {p_full}", key="btn_add_manual"):
                st.session_state['test_positions'][p_full] = {
                    "entry": entry_s, "tp": tp_s, "sl": sl_s, "tp_pct": tp_p, "sl_pct": sl_p,
                    "style": mode_actuel, "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                }
                save_data(DB_FILE, st.session_state['test_positions'])
                st.rerun()
        else:
            st.error("Paire introuvable sur KuCoin.")

# --- ONGLET 3 : JOURNAL & PERFORMANCE ---
with tab_journal:
    history = load_data(HIST_FILE)
    today = datetime.now().strftime("%d/%m")
    
    daily_pnl = 0.0
    for h in history:
        if h.get('FERMETURE', '').startswith(today):
            pnl_str = h.get('PNL %', '0').replace('+', '').replace('%', '').strip()
            try: daily_pnl += float(pnl_str)
            except: continue
    
    st.metric("PROFIT DU JOUR", f"{daily_pnl:+.2f}%", delta=f"{daily_pnl - 5:.2f}% vs Objectif 5%")
    st.progress(min(max(daily_pnl/5, 0.0), 1.0), text=f"Progression : {daily_pnl:.2f}% / 5%")
    st.write("---")

    if st.session_state['test_positions']:
        for p, data in list(st.session_state['test_positions'].items()):
            try:
                ticker = exchange.fetch_ticker(p)
                cur_p = ticker['last']
                prog = ((cur_p - data['entry']) / data['entry']) * 100
                if cur_p >= data['tp']: archive_position(p, data, data['tp'], "TP ✅"); del st.session_state['test_positions'][p]; save_data(DB_FILE, st.session_state['test_positions']); st.rerun()
                elif cur_p <= data['sl']: archive_position(p, data, data['sl'], "SL ❌"); del st.session_state['test_positions'][p]; save_data(DB_FILE, st.session_state['test_positions']); st.rerun()

                with st.expander(f"📊 {p} | {prog:+.2f}%", expanded=True):
                    c1, c2, c3 = st.columns([2, 2, 1])
                    c1.write(f"Ouvert à: {data.get('time_full', 'N/A')}")
                    c2.metric("PRIX LIVE", f"{cur_p:,.4f}", f"{prog:+.2f}%")
                    if c3.button("💰 VENDRE", key=f"sell_{p}"): archive_position(p, data, cur_p, "MANUEL"); del st.session_state['test_positions'][p]; save_data(DB_FILE, st.session_state['test_positions']); st.rerun()
            except: continue

    with st.expander("📜 VOIR L'HISTORIQUE DÉTAILLÉ", expanded=False):
        if history:
            df_hist = pd.DataFrame(history)
            cols_ordre = ["SYMBOLE", "OUVERTURE", "FERMETURE", "ENTREE", "SORTIE", "PNL %", "RAISON", "STYLE"]
            cols_valides = [c for c in cols_ordre if c in df_hist.columns]
            st.dataframe(df_hist[cols_valides].iloc[::-1], use_container_width=True)
