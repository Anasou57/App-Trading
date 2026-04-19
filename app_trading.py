import streamlit as st
import ccxt
import pandas as pd
import pandas_ta as ta
import json
import os
import time
import traceback
from datetime import datetime

# ============================================================
# PERSISTANCE & CONFIGURATION
# ============================================================
DB_FILE   = "trading_journal.json"
HIST_FILE = "trading_history.json"

def load_data(file):
    if os.path.exists(file):
        with open(file, "r") as f:
            try:    return json.load(f)
            except: return [] if "history" in file else {}
    return [] if "history" in file else {}

def save_data(file, data):
    with open(file, "w") as f:
        json.dump(data, f, indent=4)

def archive_position(symbol, data, exit_price, reason):
    history = load_data(HIST_FILE)
    pnl = ((exit_price - data['entry']) / data['entry']) * 100
    history.append({
        "SYMBOLE":   symbol,
        "OUVERTURE": data.get('time_full', "N/A"),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %":     f"{pnl:+.2f}%",
        "RAISON":    reason,
        "STYLE":     data['style'],
        "ENTREE":    f"{data['entry']:.6f}",
        "SORTIE":    f"{exit_price:.6f}",
        "RR":        data.get('rr', 'N/A'),
        "SCORE":     data.get('score', 'N/A'),
        "TYPE":      data.get('type_entree', 'N/A')
    })
    save_data(HIST_FILE, history)
    return pnl

# ============================================================
# CONNEXION EXCHANGE (KUCOIN)
# ============================================================
SPOT_OPTS = {
    'timeout': 20000,
    'enableRateLimit': True,
    'options': {'defaultType': 'spot', 'adjustForTimeDifference': True},
}

@st.cache_resource
def get_exchange():
    ex = ccxt.kucoin(SPOT_OPTS)
    ex.load_markets()
    return ex

def test_connection():
    try:
        ex = ccxt.kucoin(SPOT_OPTS)
        t  = ex.fetch_ticker('BTC/USDT')
        return ('ok', t['last'])
    except Exception as e:
        return ('err', str(e))

# ============================================================
# MOTEUR TECHNIQUE (SMC V8)
# ============================================================
def fetch_df(symbol, tf, limit=250): # Limite à 250 pour EMA200
    ex = get_exchange()
    try:
        bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
        return df.dropna()
    except: return pd.DataFrame()

def compute_indicators(df):
    if df.empty: return df
    df = df.copy()
    try:
        df['ema20']  = ta.ema(df['c'], length=20)
        df['ema50']  = ta.ema(df['c'], length=50)
        df['ema200'] = ta.ema(df['c'], length=200)
        df['rsi']    = ta.rsi(df['c'], length=14)
        df['atr']    = ta.atr(df['h'], df['l'], df['c'], length=14)
        adx = ta.adx(df['h'], df['l'], df['c'], length=14)
        df['adx'] = adx['ADX_14']
        bb = ta.bbands(df['c'], length=20, std=2)
        df['bb_upper'], df['bb_lower'] = bb['BBU_20_2.0'], bb['BBL_20_2.0']
    except: pass
    return df

def detect_structure(df):
    try:
        if df['c'].iloc[-1] > df['ema50'].iloc[-1] > df['ema200'].iloc[-1]: return "BULLISH"
        if df['c'].iloc[-1] < df['ema50'].iloc[-1] < df['ema200'].iloc[-1]: return "BEARISH"
    except: pass
    return "NEUTRAL"

def score_setup(df_htf, df_ltf, mode):
    score = 40
    try:
        last = df_ltf.iloc[-1]
        if last['c'] > last['ema50']: score += 15
        if 40 < last['rsi'] < 60: score += 10
        if last['adx'] > 20: score += 10
    except: pass
    return min(score, 100)

def get_market_analysis(symbol, mode):
    try:
        symbol = f"{symbol.upper().strip()}/USDT" if '/' not in symbol else symbol.upper()
        tf_ltf = "5m" if "SCALPING" in mode else "1h"
        tf_htf = "15m" if "SCALPING" in mode else "4h"

        df_ltf = compute_indicators(fetch_df(symbol, tf_ltf))
        df_htf = compute_indicators(fetch_df(symbol, tf_htf))
        
        if df_ltf.empty or len(df_ltf) < 200: return None, "Données insuffisantes"

        res = {
            "symbol": symbol,
            "prix": float(df_ltf['c'].iloc[-1]),
            "atr": float(df_ltf['atr'].iloc[-1]),
            "adx": float(df_ltf['adx'].iloc[-1]),
            "rsi": float(df_ltf['rsi'].iloc[-1]),
            "structure_htf": detect_structure(df_htf),
            "score": score_setup(df_htf, df_ltf, mode),
            "bb_lower": float(df_ltf['bb_lower'].iloc[-1]),
            "bb_upper": float(df_ltf['bb_upper'].iloc[-1]),
            "ema20": float(df_ltf['ema20'].iloc[-1])
        }
        return res, None
    except Exception as e: return None, str(e)

def compute_levels(res, mode):
    prix = res['prix']
    atr = res['atr']
    # Entrée optimisée SMC
    entry = res['bb_lower'] if "RANGE" in mode else min(prix, res['ema20'])
    sl = entry - (atr * 1.5)
    risk = abs(entry - sl)
    tp = entry + (risk * 2.5) # RR de 2.5 par défaut
    return {"entry": entry, "sl": sl, "tp": tp, "rr": 2.5}

# ============================================================
# STYLE & UI
# ============================================================
st.set_page_config(page_title="SMC V7.7 PRO", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #000000; color: #00FF41; font-family: 'Consolas', monospace; }
    .stMetric { background-color: #0a0a0a; border: 1px solid #00FF41; padding: 15px; border-radius: 10px; }
    .stButton>button { border: 1px solid #00FF41; background-color: #050505; color: #00FF41; }
    .stButton>button:hover { background-color: #00FF41; color: black; }
    </style>
""", unsafe_allow_html=True)

if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

# ============================================================
# LOGIQUE D'AFFICHAGE
# ============================================================
st.title("📟 SMC PERFORMANCE V7.7")

# Sidebar
with st.sidebar:
    mode_actuel = st.selectbox("STYLE", ["SCALPING (5m)", "RANGE MODE", "DAY TRADING (1h)"])
    nb_pairs = st.slider("Paires", 10, 50, 30)
    if st.button("🗑️ RESET"):
        save_data(DB_FILE, {})
        st.session_state['test_positions'] = {}
        st.rerun()

tab_scan, tab_journal = st.tabs(["🔎 SCANNER", "📈 JOURNAL"])

with tab_scan:
    col_l, col_r = st.columns([1, 2])
    
    with col_l:
        if st.button("🚀 LANCER SCAN"):
            ex = get_exchange()
            tickers = ex.fetch_tickers()
            pairs = sorted([s for s in tickers if s.endswith('/USDT')], 
                           key=lambda x: tickers[x].get('quoteVolume') or 0, reverse=True)[:nb_pairs]
            
            results = []
            for p in pairs:
                ana, err = get_market_analysis(p, mode_actuel)
                if ana: results.append(ana)
            st.session_state['scan_res'] = results

        if 'scan_res' in st.session_state:
            for r in st.session_state['scan_res']:
                if st.button(f"{r['symbol']} | Score: {r['score']}"):
                    st.session_state['active_p'] = r['symbol']

    with col_r:
        if 'active_p' in st.session_state:
            res, err = get_market_analysis(st.session_state['active_p'], mode_actuel)
            if res:
                levels = compute_levels(res, mode_actuel)
                st.header(f"💼 {res['symbol']}")
                
                # Zone Prix
                c1, c2, c3 = st.columns(3)
                c1.error(f"STOP LOSS\n{levels['sl']:,.4f}")
                c2.warning(f"ENTRÉE OPTI\n{levels['entry']:,.4f}")
                c3.success(f"TARGET TP\n{levels['tp']:,.4f}")

                st.write("### 🛒 Choisir type d'entrée :")
                b1, b2 = st.columns(2)
                
                if b1.button(f"🚀 MARCHÉ (@{res['prix']:,.4f})"):
                    # Ajustement TP pour garder le RR de 2.5
                    new_risk = abs(res['prix'] - levels['sl'])
                    st.session_state['test_positions'][res['symbol']] = {
                        "entry": res['prix'], "sl": levels['sl'], "tp": res['prix'] + (new_risk * 2.5),
                        "style": mode_actuel, "score": res['score'], "type_entree": "MARCHÉ", "rr": 2.5,
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

                if b2.button(f"⏳ LIMITE (@{levels['entry']:,.4f})"):
                    st.session_state['test_positions'][res['symbol']] = {
                        "entry": levels['entry'], "sl": levels['sl'], "tp": levels['tp'],
                        "style": mode_actuel, "score": res['score'], "type_entree": "LIMITE", "rr": 2.5,
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

with tab_journal:
    history = load_data(HIST_FILE)
    if st.session_state['test_positions']:
        for p, d in list(st.session_state['test_positions'].items()):
            # Monitoring simple
            with st.expander(f"📊 {p} ({d['type_entree']})", expanded=True):
                st.write(f"Entrée: {d['entry']} | SL: {d['sl']} | TP: {d['tp']}")
                if st.button(f"Fermer {p}"):
                    archive_position(p, d, d['entry'], "MANUEL")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()
    
    if history:
        st.table(pd.DataFrame(history).iloc[::-1])
