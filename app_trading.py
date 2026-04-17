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
                return json.load(f)
            except:
                return [] if "history" in file else {}
    return [] if "history" in file else {}

def save_data(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

def archive_position(symbol, data, exit_price, reason):
    history = load_data(HIST_FILE)
    pnl = ((exit_price - data['entry']) / data['entry']) * 100
    history.append({
        "SYMBOLE": symbol,
        "OUVERTURE": data.get('time_full', "N/A"),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %": f"{pnl:+.2f}%",
        "RAISON": reason,
        "STYLE": data['style'],
        "ENTREE": f"{data['entry']:.4f}",
        "SORTIE": f"{exit_price:.4f}"
    })
    save_data(HIST_FILE, history)
    return pnl

# --- CONFIGURATION INITIALE ---
st.set_page_config(page_title="SMC V7.7 STABLE", layout="wide")
if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

exchange = ccxt.kucoin({'timeout': 30000, 'enableRateLimit': True})

# --- MOTEUR D'ANALYSE (FILTRES ASSOUPLIS) ---
def get_market_analysis(symbol, mode_choisi):
    try:
        tf = "5m" if "SCALPING" in mode_choisi or "RANGE" in mode_choisi else "1h"
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        if not bars or len(bars) < 30: return None # Plus tolérant sur l'historique
        
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        
        # Indicateurs
        adx_df = ta.adx(df['h'], df['l'], df['c'], length=14)
        if adx_df is None: return None
        
        adx = adx_df['ADX_14'].iloc[-1]
        bb = ta.bbands(df['c'], length=20, std=2)
        prix = df['c'].iloc[-1]
        atr = ta.atr(df['h'], df['l'], df['c'], length=14).iloc[-1]
        
        return {
            "symbol": symbol, "prix": prix, "adx": adx, 
            "is_range": adx < 25, # Seuil monté à 25 pour capter plus de ranges
            "atr": atr,
            "upper": bb['BBU_20_2.0'].iloc[-1], "lower": bb['BBL_20_2.0'].iloc[-1]
        }
    except: return None

# --- STYLE ---
st.markdown("""
    <style>
    .main { background-color: #000000; color: #00FF41; font-family: 'Consolas', monospace; }
    .stMetric { background-color: #0a0a0a; border: 1px solid #00FF41; padding: 15px; border-radius: 10px; }
    .stTabs [data-baseweb="tab"] { color: #00FF41; font-weight: bold; font-size: 18px; }
    .stButton>button { border: 1px solid #00FF41; background-color: #050505; color: #00FF41; width: 100%; }
    </style>
    """, unsafe_allow_html=True)

st.title("📟 SMC PERFORMANCE V7.7")

# Test connexion
try:
    test_p = exchange.fetch_ticker('BTC/USDT')['last']
    st.success(f"✅ KUCOIN CLOUD OK | BTC: {test_p}$")
except:
    st.error("❌ Erreur API")

tab_scan, tab_search, tab_journal = st.tabs(["🔎 SCANNER INTELLIGENT", "🔍 RECHERCHE PAIRE", "📈 JOURNAL"])

with st.sidebar:
    st.header("⚙️ CONFIG")
    mode_actuel = st.selectbox("CHOISIR STYLE", ["SCALPING (5m)", "RANGE MODE (Rebond)", "DAY TRADING (1h)"])
    if st.button("🔄 ACTUALISER"): st.rerun()

# --- SCANNER ---
with tab_scan:
    col_list, col_focus = st.columns([1, 2])
    with col_list:
        if st.button("🚀 LANCER SCAN"):
            with st.status("Recherche élargie..."):
                tickers = exchange.fetch_tickers()
                valid_pairs = []
                for s, t in tickers.items():
                    if s.endswith('/USDT'):
                        # On prend le volume quote ou l'info brute
                        v = t.get('quoteVolume') or t.get('info', {}).get('volValue', 0)
                        if v: valid_pairs.append({'s': s, 'v': float(v)})
                
                # Analyse du Top 50 volume
                top_50 = sorted(valid_pairs, key=lambda x: x['v'], reverse=True)[:50]
                
                results = []
                for p_item in top_50:
                    ana = get_market_analysis(p_item['s'], mode_actuel)
                    if ana:
                        is_range_mode = "RANGE" in mode_actuel
                        # On valide si le mode correspond
                        if (is_range_mode and ana['is_range']) or (not is_range_mode and not ana['is_range']):
                            results.append(p_item['s'])
                st.session_state['scan_res'] = results

        if 'scan_res' in st.session_state:
            if not st.session_state['scan_res']:
                st.warning("Aucun signal. Essayez 'RANGE MODE'.")
            for p in st.session_state['scan_res']:
                if st.button(f"🎯 {p}"): st.session_state['active_p'] = p

    with col_focus:
        if 'active_p' in st.session_state:
            res = get_market_analysis(st.session_state['active_p'], mode_actuel)
            if res:
                st.header(f"💼 {res['symbol']}")
                entry = res['lower'] if "RANGE" in mode_actuel else res['prix']
                tp = res['upper'] if "RANGE" in mode_actuel else entry + (res['atr'] * 4.0)
                sl = entry - (res['atr'] * 1.8)
                
                c1, c2, c3 = st.columns(3)
                c1.error(f"SL: {sl:,.4f}")
                c2.warning(f"ENTRY: {entry:,.4f}")
                c3.success(f"TP: {tp:,.4f}")
                
                if st.button("🚀 AJOUTER AU JOURNAL"):
                    st.session_state['test_positions'][res['symbol']] = {
                        "entry": entry, "tp": tp, "sl": sl, "style": mode_actuel,
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.success("Position enregistrée")

# --- RECHERCHE ---
with tab_search:
    pair_input = st.text_input("Ex: BTC, ETH, SOL").upper().strip()
    if pair_input:
        ana = get_market_analysis(pair_input, mode_actuel)
        if ana:
            st.metric("PRIX", f"{ana['prix']}", f"ADX: {ana['adx']:.1f}")
            if st.button(f"🚀 SURVEILLER {ana['symbol']}"):
                st.session_state['test_positions'][ana['symbol']] = {"entry": ana['prix'], "tp": ana['prix']*1.05, "sl": ana['prix']*0.98, "style": mode_actuel, "time_full": datetime.now().strftime("%H:%M:%S")}
                save_data(DB_FILE, st.session_state['test_positions'])

# --- JOURNAL ---
with tab_journal:
    history = load_data(HIST_FILE)
    if st.session_state['test_positions']:
        for p, d in list(st.session_state['test_positions'].items()):
            try:
                curr = exchange.fetch_ticker(p)['last']
                pnl = ((curr - d['entry']) / d['entry']) * 100
                with st.expander(f"📊 {p} | {pnl:+.2f}%"):
                    if st.button(f"VENDRE {p}"):
                        archive_position(p, d, curr, "MANUEL")
                        del st.session_state['test_positions'][p]
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()
            except: continue
    if history:
        st.dataframe(pd.DataFrame(history)[::-1], use_container_width=True)
