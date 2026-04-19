import streamlit as st
import ccxt
import pandas as pd
import pandas_ta as ta
import json
import os
import time
from datetime import datetime

# ============================================================
# CONFIGURATION & PERSISTANCE (STYLE V7.7)
# ============================================================
DB_FILE = "trading_journal.json"
HIST_FILE = "trading_history.json"

def load_data(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try: return json.load(f)
            except: return [] if "history" in file else {}
    return [] if "history" in file else {}

def save_data(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

def archive_position(symbol, data, exit_price, reason):
    history = load_data(HIST_FILE)
    pnl = ((exit_price - data['entry']) / data['entry']) * 100
    history.append({
        "SYMBOLE": symbol,
        "MARCHÉ": data.get('market_type', 'SPOT'),
        "OUVERTURE": data.get('time_full', "N/A"),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %": f"{pnl:+.2f}%",
        "RAISON": reason,
        "STYLE": data['style'],
        "ENTREE": f"{data['entry']:.6f}",
        "SORTIE": f"{exit_price:.6f}",
        "SCORE": data.get('score', 'N/A')
    })
    save_data(HIST_FILE, history)
    return pnl

# ============================================================
# INITIALISATION EXCHANGES (KUCOIN)
# ============================================================
st.set_page_config(page_title="SMC PERFORMANCE V7.7 PRO", layout="wide")

exchange_spot = ccxt.kucoin({'timeout': 20000, 'enableRateLimit': True})
exchange_futures = ccxt.kucoinfutures({'timeout': 20000, 'enableRateLimit': True})

if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

# ============================================================
# MOTEUR D'ANALYSE AVANCÉ (V8 FILTERS)
# ============================================================
def compute_indicators(df):
    try:
        df['ema50'] = ta.ema(df['c'], length=50)
        df['ema200'] = ta.ema(df['c'], length=200)
        df['rsi'] = ta.rsi(df['c'], length=14)
        df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)
        adx_df = ta.adx(df['h'], df['l'], df['c'], length=14)
        df['adx'] = adx_df['ADX_14'] if adx_df is not None else 20
        bb = ta.bbands(df['c'], length=20, std=2)
        df['bb_upper'] = bb['BBU_20_2.0']
        df['bb_lower'] = bb['BBL_20_2.0']
        return df
    except: return df

def detect_structure(df):
    try:
        if df['c'].iloc[-1] > df['ema50'].iloc[-1] > df['ema200'].iloc[-1]: return "BULLISH"
        if df['c'].iloc[-1] < df['ema50'].iloc[-1] < df['ema200'].iloc[-1]: return "BEARISH"
    except: pass
    return "NEUTRAL"

def get_market_analysis(ex_obj, symbol, mode, mkt_type="SPOT"):
    try:
        tf = "5m" if "SCALPING" in mode else "1h"
        htf = "15m" if "SCALPING" in mode else "4h"
        
        bars = ex_obj.fetch_ohlcv(symbol, timeframe=tf, limit=100)
        df = compute_indicators(pd.DataFrame(bars, columns=['t','o','h','l','c','v']))
        
        bars_h = ex_obj.fetch_ohlcv(symbol, timeframe=htf, limit=100)
        df_h = compute_indicators(pd.DataFrame(bars_h, columns=['t','o','h','l','c','v']))
        
        struct_htf = detect_structure(df_h)
        prix = df['c'].iloc[-1]
        adx = df['adx'].iloc[-1]
        
        # Calcul du score SMC simplifié
        score = 40
        if struct_htf == "BULLISH" and prix > df['ema50'].iloc[-1]: score += 20
        if struct_htf == "BEARISH" and prix < df['ema50'].iloc[-1]: score += 20
        if 40 < df['rsi'].iloc[-1] < 60: score += 10
        
        return {
            "symbol": symbol, "prix": prix, "adx": adx, "score": score,
            "struct": struct_htf, "market_type": mkt_type,
            "upper": df['bb_upper'].iloc[-1], "lower": df['bb_lower'].iloc[-1],
            "atr": df['atr'].iloc[-1]
        }
    except: return None

# ============================================================
# STYLE VISUEL (V7.7)
# ============================================================
st.markdown("""
    <style>
    .main { background-color: #000000; color: #00FF41; font-family: 'Consolas', monospace; }
    .stMetric { background-color: #0a0a0a; border: 1px solid #00FF41; padding: 15px; border-radius: 10px; }
    .stTabs [data-baseweb="tab"] { color: #00FF41; font-weight: bold; font-size: 18px; }
    .stButton>button { border: 1px solid #00FF41; background-color: #050505; color: #00FF41; width: 100%; }
    .stButton>button:hover { background-color: #00FF41; color: black; }
    </style>
    """, unsafe_allow_html=True)

# ============================================================
# INTERFACE PRINCIPALE
# ============================================================
st.title("📟 SMC PERFORMANCE V7.7 PRO")

# Test Connexion
col_c1, col_c2 = st.columns(2)
try:
    p_s = exchange_spot.fetch_ticker('BTC/USDT')['last']
    col_c1.success(f"✅ SPOT OK | BTC: {p_s}$")
except: col_c1.error("❌ SPOT KO")

try:
    p_f = exchange_futures.fetch_ticker('XBTUSDTM')['last']
    col_c2.success(f"✅ FUTURES OK | BTC: {p_f}$")
except: col_c2.warning("⚠️ FUTURES KO")

tab_scan, tab_journal = st.tabs(["🔎 SCANNER INTELLIGENT", "📈 JOURNAL & PERFORMANCE"])

with st.sidebar:
    st.header("⚙️ CONFIGURATION")
    mode_actuel = st.selectbox("STYLE", ["SCALPING (5m)", "RANGE MODE", "DAY TRADING (1h)"])
    nb_scan = st.slider("Paires à scanner", 20, 100, 40)
    if st.button("🔄 ACTUALISER TOUT"): st.rerun()

# --- ONGLET 1 : SCANNER ---
with tab_scan:
    c_list, c_focus = st.columns([1, 2])
    
    with c_list:
        if st.button(f"🚀 LANCER SCAN"):
            with st.status("Analyse Flux KuCoin..."):
                # Récupération Tickers
                t_s = exchange_spot.fetch_tickers()
                pairs = sorted([s for s in t_s if s.endswith('/USDT
