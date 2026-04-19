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
# PERSISTANCE
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
        "OUVERTURE": data.get('time_full', data.get('time', "N/A")),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %":     f"{pnl:+.2f}%",
        "RAISON":    reason,
        "STYLE":     data['style'],
        "TYPE":      data.get('type_entree', 'N/A'),
        "ENTREE":    f"{data['entry']:.4f}",
        "SORTIE":    f"{exit_price:.4f}",
        "RR":        data.get('rr', 'N/A'),
        "SCORE":     data.get('score', 'N/A'),
        "pnl":       round(pnl, 2),
        "date":      datetime.now().strftime("%Y-%m-%d"),
    })
    save_data(HIST_FILE, history)
    return pnl

# ============================================================
# EXCHANGE — SPOT UNIQUEMENT
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

@st.cache_data(ttl=60)
def test_connection():
    try:
        ex = ccxt.kucoin(SPOT_OPTS)
        t  = ex.fetch_ticker('BTC/USDT')
        return ('ok', t['last'])
    except Exception as e:
        return ('err', str(e))

# ============================================================
# MOTEUR D'ANALYSE V8
# ============================================================
def fetch_df(symbol, tf, limit=250):
    ex = get_exchange()
    for attempt in range(2):
        try:
            bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
            if not bars or len(bars) < 30:
                raise ValueError(f"Données insuffisantes ({len(bars) if bars else 0} bougies)")
            df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
            return df.dropna()
        except Exception as e:
            if attempt == 0: time.sleep(0.5)
            else: raise e

def compute_indicators(df):
    df = df.copy()
    try: df['ema20']  = ta.ema(df['c'], length=20)
    except: df['ema20'] = None
    try: df['ema50']  = ta.ema(df['c'], length=50)
    except: df['ema50'] = None
    try: df['ema200'] = ta.ema(df['c'], length=200)
    except: df['ema200'] = None
    try: df['rsi']    = ta.rsi(df['c'], length=14)
    except: df['rsi'] = None
    try:
        macd = ta.macd(df['c'], fast=12, slow=26, signal=9)
        if macd is not None:
            df['macd']        = macd.iloc[:, 0]
            df['macd_signal'] = macd.iloc[:, 2]
            df['macd_hist']   = macd.iloc[:, 1]
    except: pass
    try: df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)
    except: df['atr'] = df['c'] * 0.01
    try:
        bb = ta.bbands(df['c'], length=20, std=2)
        if bb is not None:
            df['bb_upper'] = bb.iloc[:, 0]
            df['bb_lower'] = bb.iloc[:, 2]
            df['bb_mid']   = bb.iloc[:, 1]
            df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']
    except: pass
    try:
        adx_data = ta.adx(df['h'], df['l'], df['c'], length=14)
        if adx_data is not None:
            df['adx'] = adx_data.iloc[:, 0]
            df['dmp'] = adx_data.iloc[:, 1]
            df['dmn'] = adx_data.iloc[:, 2]
    except: pass
    try:
        df['vol_sma20'] = df['v'].rolling(20).mean()
        df['vol_ratio'] = df['v'] / df['vol_sma20']
    except: pass
    return df

def detect_structure(df):
    try:
        highs = df['h'].rolling(5, center=True).max()
        lows  = df['l'].rolling(5, center=True).min()
        rh = [df['h'].iloc[i] for i in range(max(0, len(df)-20), len(df)) if df['h'].iloc[i] == highs.iloc[i]]
        rl = [df['l'].iloc[i] for i in range(max(0, len(df)-20), len(df)) if df['l'].iloc[i] == lows.iloc[i]]
        if len(rh) >= 2 and len(rl) >= 2:
            if rh[-1] > rh[-2] and rl[-1] > rl[-2]: return "BULLISH"
            if rh[-1] < rh[-2] and rl[-1] < rl[-2]: return "BEARISH"
    except: pass
    return "NEUTRAL"

def detect_ob_fvg(df):
    ob_bull, ob_bear, fvg = None, None, None
    try:
        for i in range(max(0, len(df)-10), len(df)-1):
            if df['c'].iloc[i] < df['o'].iloc[i] and df['c'].iloc[i+1] > df['o'].iloc[i+1]:
                if df['c'].iloc[i+1] > df['h'].iloc[i]: ob_bull = float(df['l'].iloc[i])
            if df['c'].iloc[i] > df['o'].iloc[i] and df['c'].iloc[i+1] < df['o'].iloc[i+1]:
                if df['c'].iloc[i+1] < df['l'].iloc[i]: ob_bear = float(df['h'].iloc[i])
    except: pass
    return ob_bull, ob_bear, fvg

def score_setup(r_htf, r_ltf, mode):
    score = 0
    c    = r_ltf.get('c', 0) or 0
    e20  = r_ltf.get('ema20')
    e50  = r_ltf.get('ema50')
    e200 = r_ltf.get('ema200')

    if e20 and e50 and e200:
        if c > e20 > e50 > e200:   score += 20
        elif c < e20 < e50 < e200: score += 20
        elif c > e50:              score += 10
    else: score += 5

    rsi = r_ltf.get('rsi', 50)
    if rsi:
        if 40 < rsi < 65:          score += 15
        elif rsi < 35 or rsi > 70: score += 10

    struct = r_htf.get('structure', 'NEUTRAL')
    if struct == "BULLISH" and c > (e50 or 0):        score += 20
    elif struct == "BEARISH" and c < (e50 or 999999): score += 20

    return min(score, 100)

def get_market_analysis(symbol, mode_choisi):
    try:
        symbol = symbol.upper().strip()
        if '/' not in symbol: symbol = f"{symbol}/USDT"

        if "SCALPING" in mode_choisi:   htf, ltf = "15m", "5m"
        elif "RANGE" in mode_choisi:    htf, ltf = "1h",  "15m"
        elif "DAY" in mode_choisi:      htf, ltf = "4h",  "1h"
        else:                           htf, ltf = "1d",  "4h"

        df_htf = compute_indicators(fetch_df(symbol, htf))
        df_ltf = compute_indicators(fetch_df(symbol, ltf))

        row_htf = df_htf.iloc[-1].to_dict()
        row_ltf = df_ltf.iloc[-1].to_dict()
        row_htf['structure'] = detect_structure(df_htf)

        ob_bull, ob_bear, _ = detect_ob_fvg(df_ltf)
        row_ltf['ob_bull'] = ob_bull
        row_ltf['ob_bear'] = ob_bear

        score = score_setup(row_htf, row_ltf, mode_choisi)
        
        def fv(k): return float(row_ltf[k]) if row_ltf.get(k) is not None else None

        return {
            "symbol":        symbol,
            "prix":          float(row_ltf['c']),
            "atr":           float(row_ltf.get('atr') or row_ltf['c'] * 0.01),
            "adx":           float(row_ltf.get('adx') or 20),
            "rsi":           fv('rsi'),
            "ema20":         fv('ema20'),
            "bb_lower":      fv('bb_lower'),
            "bb_upper":      fv('bb_upper'),
            "structure_htf": row_htf['structure'],
            "ob_bull":       ob_bull,
            "ob_bear":       ob_bear,
            "score":         score,
            "htf":           htf,
            "ltf":           ltf
        }, None
    except Exception as e: return None, str(e)

def compute_levels(res, mode_choisi):
    prix = res['prix']
    atr  = res['atr']
    
    # Entrée OPTIMALE (SMC/Range)
    if "RANGE" in mode_choisi and res.get('bb_lower'):
        entry = res['ob_bull'] if res['ob_bull'] else res['bb_lower']
    elif res['structure_htf'] == "BULLISH" and res.get('ema20'):
        entry = min(prix, res['ema20'])
    else: entry = prix

    sl = entry - atr * 1.5
    risk = abs(entry - sl)
    tp = entry + risk * 2.5 # RR cible
    
    return {
        "entry": entry, "sl": sl, "tp": tp, 
        "rr": round(abs(tp-entry)/risk, 2),
        "risk_pct": ((sl-entry)/entry)*100,
        "tp_pct": ((tp-entry)/entry)*100
    }

# ============================================================
# STYLE & UI
# ============================================================
st.set_page_config(page_title="SMC PERFORMANCE V7.7", layout="wide")
st.markdown("""
    <style>
    .main { background-color: #000000; color: #00FF41; font-family: 'Consolas', monospace; }
    .stMetric { background-color: #0a0a0a; border: 1px solid #00FF41; padding: 15px; border-radius: 10px; }
    .stTabs [data-baseweb="tab"] { color: #00FF41; font-weight: bold; }
    .stButton>button { border: 1px solid #00FF41; background-color: #050505; color: #00FF41; width: 100%; }
    .stButton>button:hover { background-color: #00FF41; color: black; }
    </style>
""", unsafe_allow_html=True)

# Session State
if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

# Header
cx = test_connection()
st.title("📟 SMC PERFORMANCE V7.7")
if cx[0] == 'ok': st.success(f"✅ KUCOIN OK | BTC: {cx[1]:,.2f}$")

tab_scan, tab_search, tab_journal = st.tabs(["🔎 SCANNER", "🔍 RECHERCHE", "📈 JOURNAL"])

with st.sidebar:
    st.header("⚙️ CONFIG")
    mode_actuel = st.selectbox("STYLE", ["SCALPING (5m)", "RANGE MODE", "DAY TRADING (1h)"])
    score_min = st.slider("Min Score", 20, 80, 40)
    nb_pairs = st.slider("Pairs", 10, 80, 40)
    if st.button("🗑️ RESET"):
        save_data(DB_FILE, {})
        st.session_state['test_positions'] = {}
        st.rerun()

# --- SCANNER ---
with tab_scan:
    col_list, col_focus = st.columns([1, 2])
    with col_list:
        if st.button("LANCER SCAN"):
            ex = get_exchange()
            tickers = ex.fetch_tickers()
            pairs = sorted([s for s in tickers if s.endswith('/USDT')], 
                           key=lambda x: tickers[x].get('quoteVolume') or 0, reverse=True)[:nb_pairs]
            
            valid = []
            for p in pairs:
                ana, err = get_market_analysis(p, mode_actuel)
                if ana and ana['score'] >= score_min: valid.append(ana)
            st.session_state['scan_res'] = valid

        if 'scan_res' in st.session_state:
            for r in st.session_state['scan_res']:
                if st.button(f"🎯 {r['symbol']} | {r['score']}/100"):
                    st.session_state['active_p'] = r['symbol']

    with col_focus:
        if 'active_p' in st.session_state:
            p = st.session_state['active_p']
            res, err = get_market_analysis(p, mode_actuel)
            if res:
                levels = compute_levels(res, mode_actuel)
                st.header(f"💼 Analyse : {p}")
                st.caption(f"Score: {res['score']} | HTF: {res['structure_htf']}")
                
                c1, c2, c3 = st.columns(3)
                c1.error(f"STOP LOSS\n{levels['sl']:,.4f}")
                c2.warning(f"PRIX OPTI\n{levels['entry']:,.4f}")
                c3.success(f"TARGET TP\n{levels['tp']:,.4f}")

                st.write("### 🛒 Comment souhaites-tu entrer ?")
                b1, b2 = st.columns(2)
                
                if b1.button(f"🚀 MARCHÉ (@{res['prix']:,.4f})"):
                    risk = abs(res['prix'] - levels['sl'])
                    st.session_state['test_positions'][p] = {
                        "symbol": p, "entry": res['prix'], "sl": levels['sl'], 
                        "tp": res['prix'] + (risk * 2.5), "style": mode_actuel, 
                        "score": res['score'], "type_entree": "MARCHÉ", "rr": 2.5,
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions']); st.rerun()

                if b2.button(f"⏳ LIMITE (@{levels['entry']:,.4f})"):
                    st.session_state['test_positions'][p] = {
                        "symbol": p, "entry": levels['entry'], "sl": levels['sl'], 
                        "tp": levels['tp'], "style": mode_actuel, 
                        "score": res['score'], "type_entree": "LIMITE", "rr": 2.5,
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions']); st.rerun()

# --- RECHERCHE ---
with tab_search:
    pair_in = st.text_input("Symbole (ex: BTC)").upper()
    if pair_in:
        res, err = get_market_analysis(pair_in, mode_actuel)
        if res:
            levels = compute_levels(res, mode_actuel)
            st.subheader(f"Analyse {res['symbol']}")
            # Même logique de boutons pour la recherche manuelle
            m1, m2 = st.columns(2)
            if m1.button(f"🚀 MARCHÉ {res['symbol']}"):
                risk = abs(res['prix'] - levels['sl'])
                st.session_state['test_positions'][res['symbol']] = {
                    "symbol": res['symbol'], "entry": res['prix'], "sl": levels['sl'], "tp": res['prix'] + (risk * 2.5),
                    "style": mode_actuel, "score": res['score'], "type_entree": "MARCHÉ", "rr": 2.5,
                    "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                }
                save_data(DB_FILE, st.session_state['test_positions']); st.rerun()
            if m2.button(f"⏳ LIMITE {res['symbol']}"):
                st.session_state['test_positions'][res['symbol']] = {
                    "symbol": res['symbol'], "entry": levels['entry'], "sl": levels['sl'], "tp": levels['tp'],
                    "style": mode_actuel, "score": res['score'], "type_entree": "LIMITE", "rr": 2.5,
                    "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                }
                save_data(DB_FILE, st.session_state['test_positions']); st.rerun()

# --- JOURNAL ---
with tab_journal:
    history = load_data(HIST_FILE)
    if st.session_state['test_positions']:
        st.subheader("Positions Actives")
        for p, d in list(st.session_state['test_positions'].items()):
            with st.expander(f"📊 {p} | {d['type_entree']}"):
                st.write(f"Entrée: {d['entry']:.6f} | SL: {d['sl']:.6f} | TP: {d['tp']:.6f}")
                if st.button(f"Fermer {p}", key=f"close_{p}"):
                    archive_position(p, d, d['entry'], "MANUEL")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions']); st.rerun()
    if history:
        st.subheader("Historique")
        st.dataframe(pd.DataFrame(history).iloc[::-1], use_container_width=True)
