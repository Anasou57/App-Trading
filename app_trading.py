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

def archive_position(symbol, data, exit_price, reason, current_res=None):
    history = load_data(HIST_FILE)
    pnl = ((exit_price - data['entry']) / data['entry']) * 100
    
    # --- DIAGNOSTIC AUTO ---
    diag = "N/A"
    metrics = {}
    if current_res and "SL" in reason:
        metrics = {
            "rsi": current_res.get('rsi'),
            "adx": current_res.get('adx'),
            "struct": current_res.get('structure_htf'),
            "vol": current_res.get('vol_ratio')
        }
        if current_res['adx'] < 20: diag = "Range sans force (ADX bas)"
        elif current_res['structure_htf'] != data.get('style_struct', 'BULLISH'): diag = "Retournement de Tendance HTF"
        elif current_res.get('vol_ratio', 1) < 0.8: diag = "Manque de Volume / Capitulation"
        else: diag = "Mèche de Liquidation (Stop Hunt)"

    history.append({
        "SYMBOLE":   symbol,
        "OUVERTURE": data.get('time_full', "N/A"),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %":     f"{pnl:+.2f}%",
        "RAISON":    reason,
        "DIAGNOSTIC": diag,
        "METRICS":   metrics,
        "STYLE":     data['style'],
        "ENTREE":    data['entry'],
        "SORTIE":    exit_price,
        "SCORE_INIT": data.get('score', 'N/A'),
        "pnl_val":   round(pnl, 2)
    })
    save_data(HIST_FILE, history)
    return pnl

# ============================================================
# EXCHANGE
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

# ============================================================
# MOTEUR D'ANALYSE
# ============================================================
def fetch_df(symbol, tf, limit=150):
    ex = get_exchange()
    try:
        bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
        df = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
        return df.dropna()
    except: return pd.DataFrame()

def compute_indicators(df):
    if df.empty: return df
    df = df.copy()
    df['rsi'] = ta.rsi(df['c'], length=14)
    df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)
    adx_df = ta.adx(df['h'], df['l'], df['c'], length=14)
    if adx_df is not None: df['adx'] = adx_df.iloc[:, 0]
    bb = ta.bbands(df['c'], length=20)
    if bb is not None:
        df['bb_lower'] = bb.iloc[:, 0]
        df['bb_upper'] = bb.iloc[:, 2]
    df['ema20'] = ta.ema(df['c'], length=20)
    df['vol_sma20'] = df['v'].rolling(20).mean()
    df['vol_ratio'] = df['v'] / df['vol_sma20']
    return df

def detect_structure(df):
    try:
        if len(df) < 20: return "NEUTRAL"
        last_h = df['h'].iloc[-10:].max()
        last_l = df['l'].iloc[-10:].min()
        prev_h = df['h'].iloc[-20:-10].max()
        prev_l = df['l'].iloc[-20:-10].min()
        if last_h > prev_h and last_l > prev_l: return "BULLISH"
        if last_h < prev_h and last_l < prev_l: return "BEARISH"
    except: pass
    return "NEUTRAL"

def get_market_analysis(symbol, mode_choisi):
    try:
        if '/' not in symbol: symbol = f"{symbol}/USDT"
        tf_htf, tf_ltf = ("15m", "5m") if "SCALPING" in mode_choisi else ("1h", "15m")
        
        df_htf = compute_indicators(fetch_df(symbol, tf_htf))
        df_ltf = compute_indicators(fetch_df(symbol, tf_ltf))
        
        if df_htf.empty or df_ltf.empty: return None, "Data Error"

        row_ltf = df_ltf.iloc[-1]
        struct_htf = detect_structure(df_htf)
        
        score = 50
        if struct_htf == "BULLISH": score += 20
        if row_ltf['rsi'] < 40: score += 10
        if row_ltf.get('vol_ratio', 1) > 1.2: score += 10

        return {
            "symbol": symbol, "prix": float(row_ltf['c']), "atr": float(row_ltf['atr']),
            "rsi": float(row_ltf['rsi']), "adx": float(row_ltf.get('adx', 20)),
            "vol_ratio": float(row_ltf.get('vol_ratio', 1)), "structure_htf": struct_htf,
            "score": score, "bb_lower": row_ltf.get('bb_lower'), "ema20": row_ltf.get('ema20'),
            "htf": tf_htf, "ltf": tf_ltf
        }, None
    except Exception as e: return None, str(e)

def compute_levels(res, mode_choisi):
    prix, atr = res['prix'], res['atr']
    entry = res['ema20'] if res['structure_htf'] == "BULLISH" else prix
    sl = entry - (atr * 2.2)  # SL élargi
    risk = abs(entry - sl)
    tp = entry + (risk * 2.1) # RR de 2.1
    return {
        "entry": entry, "sl": sl, "tp": tp,
        "risk_pct": ((sl - entry) / entry) * 100,
        "tp_pct": ((tp - entry) / entry) * 100,
        "rr": 2.1
    }

# ============================================================
# INTERFACE STREAMLIT
# ============================================================
st.set_page_config(page_title="SMC V7.7 PRO", layout="wide")
st.markdown("<style>.main { background-color: #000; color: #00FF41; font-family: monospace; }</style>", unsafe_allow_html=True)

if 'test_positions' not in st.session_state: st.session_state['test_positions'] = load_data(DB_FILE)
if 'last_monitor' not in st.session_state: st.session_state['last_monitor'] = 0

# --- AUTO MONITOR ---
now = time.time()
if now - st.session_state['last_monitor'] > 60 and st.session_state['test_positions']:
    st.session_state['last_monitor'] = now
    changed = False
    for p, data in list(st.session_state['test_positions'].items()):
        res_live, _ = get_market_analysis(p, data['style'])
        if res_live:
            cur_p = res_live['prix']
            if cur_p >= data['tp']:
                archive_position(p, data, data['tp'], "TP ✅", res_live)
                del st.session_state['test_positions'][p]; changed = True
            elif cur_p <= data['sl']:
                archive_position(p, data, data['sl'], "SL ❌", res_live)
                del st.session_state['test_positions'][p]; changed = True
    if changed: save_data(DB_FILE, st.session_state['test_positions']); st.rerun()

tab_scan, tab_search, tab_journal, tab_details = st.tabs(["🔎 SCANNER", "🔍 RECHERCHE", "📈 JOURNAL", "🔬 ANALYSE ÉCHECS (SL)"])

with tab_scan:
    col_list, col_focus = st.columns([1, 2])
    with col_list:
        mode_actuel = st.selectbox("STYLE", ["SCALPING (5m)", "DAY TRADING (15m)"])
        if st.button("LANCER SCAN"):
            # Simulation de scan (simplifiée pour l'exemple)
            st.session_state['scan_res'] = [{"sym": "BTC/USDT", "score": 85, "structure": "BULLISH"}]
        
        if 'scan_res' in st.session_state:
            for r in st.session_state['scan_res']:
                is_open = r['sym'] in st.session_state['test_positions']
                badge = "📦 " if is_open else ""
                if st.button(f"{badge}{r['structure']} {r['sym']} | {r['score']}", key=r['sym']):
                    st.session_state['active_p'] = r['sym']

    with col_focus:
        if 'active_p' in st.session_state:
            p = st.session_state['active_p']
            res, _ = get_market_analysis(p, mode_actuel)
            if res:
                levels = compute_levels(res, mode_actuel)
                st.subheader(f"Analyse {p}")
                st.write(f"Entrée: {levels['entry']:.4f} | SL: {levels['sl']:.4f} | TP: {levels['tp']:.4f}")
                if st.button("🚀 OUVRIR POSITION"):
                    st.session_state['test_positions'][p] = {**levels, "style": mode_actuel, "score": res['score'], "time_full": datetime.now().strftime("%H:%M")}
                    save_data(DB_FILE, st.session_state['test_positions']); st.rerun()

with tab_journal:
    st.subheader("Positions Actives")
    st.write(st.session_state['test_positions'])
    st.subheader("Historique")
    st.dataframe(pd.DataFrame(load_data(HIST_FILE)))

with tab_details:
    st.header("🔬 Autopsie des Stop Loss")
    sl_trades = [h for h in load_data(HIST_FILE) if "SL" in str(h.get('RAISON', ''))]
    if sl_trades:
        c1, c2 = st.columns([1, 2])
        with c1:
            for i, t in enumerate(reversed(sl_trades)):
                if st.button(f"❌ {t['SYMBOLE']} ({t['FERMETURE']})", key=f"sl_{i}"):
                    st.session_state['active_diag'] = t
        with c2:
            if 'active_diag' in st.session_state:
                t = st.session_state['active_diag']
                st.error(f"DIAGNOSTIC : {t.get('DIAGNOSTIC')}")
                st.json(t.get('METRICS'))
