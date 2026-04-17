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

# --- ARCHIVAGE ---
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

# --- CONFIGURATION ---
st.set_page_config(page_title="SMC V7.7 KUCOIN STABLE", layout="wide")
if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

exchange = ccxt.kucoin({'timeout': 30000, 'enableRateLimit': True})

# --- MOTEUR D'ANALYSE (CORRIGÉ) ---
def get_market_analysis(symbol, mode_choisi):
    try:
        tf = "5m" if "SCALPING" in mode_choisi or "RANGE" in mode_choisi else "1h"
        bars = exchange.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        if not bars or len(bars) < 50: return None
        
        df = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
        
        # Calcul ADX sécurisé
        adx_df = ta.adx(df['h'], df['l'], df['c'], length=14)
        if adx_df is None or adx_df.empty: return None
        adx = adx_df['ADX_14'].iloc[-1]
        
        # Calcul BB & ATR
        bb = ta.bbands(df['c'], length=20, std=2)
        atr = ta.atr(df['h'], df['l'], df['c'], length=14).iloc[-1]
        prix = df['c'].iloc[-1]
        
        return {
            "symbol": symbol, "prix": prix, "adx": adx, 
            "is_range": adx < 25, "atr": atr,
            "upper": bb['BBU_20_2.0'].iloc[-1], "lower": bb['BBL_20_2.0'].iloc[-1]
        }
    except: return None

# --- STYLE CSS ---
st.markdown("""
    <style>
    .main { background-color: #000000; color: #00FF41; }
    .stMetric { background-color: #0a0a0a; border: 1px solid #00FF41; padding: 10px; border-radius: 5px; }
    .stButton>button { border: 1px solid #00FF41; background: black; color: #00FF41; }
    </style>
    """, unsafe_allow_html=True)

st.title("📟 SMC PERFORMANCE V7.7")

# --- SCANNER INTELLIGENT (OPTIMISÉ POUR KUCOIN) ---
tab_scan, tab_search, tab_journal = st.tabs(["🔎 SCANNER", "🔍 RECHERCHE", "📈 JOURNAL"])

with st.sidebar:
    mode_actuel = st.selectbox("STYLE", ["SCALPING (5m)", "RANGE MODE (Rebond)", "DAY TRADING (1h)"])
    if st.button("🔄 REFRESH"): st.rerun()

with tab_scan:
    col_list, col_focus = st.columns([1, 2])
    
    with col_list:
        if st.button("🚀 LANCER SCAN"):
            with st.status("Filtrage KuCoin..."):
                tickers = exchange.fetch_tickers()
                # On filtre les paires USDT et on s'assure que le volume existe
                potential = []
                for s, t in tickers.items():
                    if s.endswith('/USDT'):
                        vol = t.get('quoteVolume') or t.get('info', {}).get('volValue', 0)
                        if vol: potential.append({'s': s, 'v': float(vol)})
                
                # Top 40 par volume pour éviter de saturer l'API
                top_pairs = sorted(potential, key=lambda x: x['v'], reverse=True)[:40]
                
                scan_results = []
                for p_item in top_pairs:
                    ana = get_market_analysis(p_item['s'], mode_actuel)
                    if ana:
                        # Logique de détection
                        is_range_mode = "RANGE" in mode_actuel
                        if (is_range_mode and ana['is_range']) or (not is_range_mode and not ana['is_range']):
                            scan_results.append(p_item['s'])
                
                st.session_state['scan_res'] = scan_results

        if 'scan_res' in st.session_state:
            if not st.session_state['scan_res']:
                st.info("Aucun signal trouvé. Essayez un autre mode.")
            for p in st.session_state['scan_res']:
                if st.button(f"🎯 {p}"): st.session_state['active_p'] = p

    with col_focus:
        if 'active_p' in st.session_state:
            res = get_market_analysis(st.session_state['active_p'], mode_actuel)
            if res:
                st.subheader(f"Analyse : {res['symbol']}")
                entry = res['lower'] if "RANGE" in mode_actuel else res['prix']
                tp = res['upper'] if "RANGE" in mode_actuel else entry + (res['atr'] * 4.5)
                sl = entry - (res['atr'] * 2.0)
                
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
                    st.success("Position ajoutée !")

# --- RECHERCHE MANUELLE ---
with tab_search:
    sym = st.text_input("Symbole (ex: SOL, PEPE)").upper()
    if sym:
        ana = get_market_analysis(sym, mode_actuel)
        if ana:
            st.write(f"Prix: {ana['prix']} | ADX: {ana['adx']:.1f}")
            if st.button(f"Suivre {ana['symbol']}"):
                st.session_state['test_positions'][ana['symbol']] = {
                    "entry": ana['prix'], "tp": ana['prix']*1.05, "sl": ana['prix']*0.98,
                    "style": mode_actuel, "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                }
                save_data(DB_FILE, st.session_state['test_positions'])

# --- JOURNAL ---
with tab_journal:
    history = load_data(HIST_FILE)
    if st.session_state['test_positions']:
        for p, d in list(st.session_state['test_positions'].items()):
            try:
                curr = exchange.fetch_ticker(p)['last']
                pnl = ((curr - d['entry']) / d['entry']) * 100
                st.write(f"**{p}**: {pnl:+.2f}% (Prix: {curr})")
                if st.button(f"Fermer {p}"):
                    archive_position(p, d, curr, "MANUEL")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()
            except: continue
    
    if history:
        st.write("---")
        st.dataframe(pd.DataFrame(history)[::-1])
