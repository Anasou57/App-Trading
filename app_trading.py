Import streamlit as st
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
def fetch_df(symbol, tf, limit=150):
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
    try:
        stoch = ta.stoch(df['h'], df['l'], df['c'], k=14, d=3)
        if stoch is not None:
            df['stoch_k'] = stoch.iloc[:, 0]
            df['stoch_d'] = stoch.iloc[:, 1]
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
        for i in range(max(0, len(df)-8), len(df)-2):
            gap = df['l'].iloc[i+1] - df['h'].iloc[i-1]
            if gap > 0:   fvg = {'type': 'bullish', 'low': float(df['h'].iloc[i-1]), 'high': float(df['l'].iloc[i+1])}
            elif gap < 0: fvg = {'type': 'bearish', 'low': float(df['l'].iloc[i+1]), 'high': float(df['h'].iloc[i-1])}
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
        else:                      score += 5
    else: score += 5

    rsi = r_ltf.get('rsi', 50)
    if rsi:
        if 40 < rsi < 65:          score += 15
        elif rsi < 35 or rsi > 70: score += 10

    if r_ltf.get('macd') and r_ltf.get('macd_signal'):
        score += 15 if r_ltf['macd'] > r_ltf['macd_signal'] and (r_ltf.get('macd_hist') or 0) > 0 else 8

    vr = r_ltf.get('vol_ratio', 1) or 1
    if vr > 1.3: score += 10
    elif vr > 1.0: score += 5

    struct = r_htf.get('structure', 'NEUTRAL')
    if struct == "BULLISH" and c > (e50 or 0):        score += 20
    elif struct == "BEARISH" and c < (e50 or 999999): score += 20
    else:                                              score += 8

    if r_ltf.get('ob_bull') or r_ltf.get('ob_bear'): score += 10

    adx = r_ltf.get('adx', 20) or 20
    if "RANGE" in mode and adx < 25:       score += 10
    elif "RANGE" not in mode and adx > 20: score += 10
    elif "RANGE" not in mode and adx > 15: score += 5

    return min(score, 100)

def get_market_analysis(symbol, mode_choisi):
    """Retourne (result_dict, error_str)."""
    try:
        symbol = symbol.upper().strip()
        if '/' not in symbol:
            symbol = f"{symbol}/USDT"

        if "SCALPING" in mode_choisi:   htf, ltf = "15m", "5m"
        elif "RANGE" in mode_choisi:    htf, ltf = "1h",  "15m"
        elif "DAY" in mode_choisi:      htf, ltf = "4h",  "1h"
        else:                           htf, ltf = "1d",  "4h"

        df_htf = compute_indicators(fetch_df(symbol, htf))
        df_ltf = compute_indicators(fetch_df(symbol, ltf))

        row_htf = df_htf.iloc[-1].to_dict()
        row_ltf = df_ltf.iloc[-1].to_dict()
        row_htf['structure'] = detect_structure(df_htf)

        ob_bull, ob_bear, fvg = detect_ob_fvg(df_ltf)
        row_ltf['ob_bull'] = ob_bull
        row_ltf['ob_bear'] = ob_bear

        score   = score_setup(row_htf, row_ltf, mode_choisi)
        adx_val = float(row_ltf.get('adx') or 20)

        def fv(k): return float(row_ltf[k]) if row_ltf.get(k) is not None else None

        return {
            "symbol":        symbol,
            "prix":          float(row_ltf['c']),
            "atr":           float(row_ltf.get('atr') or row_ltf['c'] * 0.01),
            "adx":           adx_val,
            "rsi":           fv('rsi'),
            "macd_hist":     fv('macd_hist'),
            "ema20":         fv('ema20'),
            "ema50":         fv('ema50'),
            "ema200":        fv('ema200'),
            "bb_upper":      fv('bb_upper'),
            "bb_lower":      fv('bb_lower'),
            "bb_width":      fv('bb_width'),
            "vol_ratio":     float(row_ltf.get('vol_ratio') or 1),
            "stoch_k":       fv('stoch_k'),
            "structure_htf": row_htf['structure'],
            "structure_ltf": detect_structure(df_ltf),
            "ob_bull":       ob_bull,
            "ob_bear":       ob_bear,
            "fvg":           fvg,
            "is_range":      adx_val < 22,
            "score":         score,
            "htf":           htf,
            "ltf":           ltf,
            "upper":         fv('bb_upper'),
            "lower":         fv('bb_lower'),
        }, None

    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)}"

def compute_levels(res, mode_choisi):
    prix = res['prix']
    atr  = res['atr'] or prix * 0.01

    # ── ENTRÉE ── (Reste inchangé)
    if "RANGE" in mode_choisi and res.get('bb_lower'):
        entry = res['ob_bull'] if res['ob_bull'] else res['bb_lower']
    elif res['structure_htf'] == "BULLISH" and res.get('ema20'):
        entry = min(prix, res['ema20'])
    else:
        entry = prix
    if not entry: entry = prix

    # ── STOP LOSS (Ajusté de 1.5 à 2.2 pour plus de marge) ──
    # On passe de 1.5 à 2.2 pour éviter les chasses aux liquidités
    sl_atr = entry - (atr * 2.2) 
    
    if res.get('ob_bull'):    
        # On place le SL un peu sous l'Order Block, mais pas trop loin non plus
        sl = min(sl_atr, res['ob_bull'] - (atr * 0.5))
    elif res.get('bb_lower'): 
        sl = min(sl_atr, res['bb_lower'] * 0.995) # Marge de 0.5% sous la BB
    else:                     
        sl = sl_atr

    risk = abs(entry - sl)
    if risk <= 0: risk = atr * 2.2

    # ── TP (On garde un RR de 2.0 minimum pour compenser le SL plus large) ──
    tp_raw = entry + (risk * 2.1) # On monte un poil le RR à 2.1

    # Minimum absolu selon le timeframe (pour éviter les TP trop minuscules)
    if "SCALPING" in mode_choisi or "RANGE" in mode_choisi:
        tp_min = entry * 1.02   # +2% mini
    else:
        tp_min = entry * 1.04   # +4% mini

    tp = max(tp_raw, tp_min)

    return {
        "entry":    entry,
        "sl":       sl,
        "tp":       tp,
        "tp_pct":   ((tp - entry) / entry) * 100,
        "risk_pct": ((sl - entry) / entry) * 100,
        "rr":       round(abs(tp - entry) / risk, 2),
    }

# ============================================================
# STYLE V7.7
# ============================================================
st.set_page_config(page_title="SMC PERFORMANCE V7.7 (KUCOIN)", layout="wide")

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
# SESSION STATE
# ============================================================
if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)
if 'last_monitor' not in st.session_state:
    st.session_state['last_monitor'] = 0

# ============================================================
# AUTO-MONITORING (60s)
# ============================================================
now = time.time()
if now - st.session_state['last_monitor'] > 60 and st.session_state['test_positions']:
    st.session_state['last_monitor'] = now
    changed = False
    for p, data in list(st.session_state['test_positions'].items()):
        try:
            sym   = data.get('symbol', p)
            cur_p = get_exchange().fetch_ticker(sym)['last']
            tp_check = data.get('tp', 9e15)
            if cur_p >= tp_check:
                archive_position(sym, data, tp_check, "TP ✅")
                del st.session_state['test_positions'][p]; changed = True
            elif cur_p <= data['sl']:
                archive_position(sym, data, data['sl'], "SL ❌")
                del st.session_state['test_positions'][p]; changed = True
        except: continue
    if changed:
        save_data(DB_FILE, st.session_state['test_positions'])
        st.rerun()

# ============================================================
# HEADER
# ============================================================
st.title("📟 SMC PERFORMANCE V7.7")

cx = test_connection()
if cx[0] == 'ok':
    st.success(f"✅ Connexion KUCOIN OK | BTC: {cx[1]:,.2f}$")
else:
    st.error(f"❌ Erreur de connexion API : {cx[1]}")
    if any(k in cx[1] for k in ['NetworkError', 'ExchangeNotAvailable']):
        st.info(
            "KuCoin inaccessible depuis Streamlit Cloud.\n"
            "Remplacez `ccxt.kucoin` par `ccxt.binance` dans le code."
        )

tab_scan, tab_search, tab_journal = st.tabs([
    "🔎 SCANNER INTELLIGENT", "🔍 RECHERCHE PAIRE", "📈 JOURNAL & PERFORMANCE"
])

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("⚙️ CONFIGURATION")
    mode_actuel = st.selectbox("CHOISIR STYLE", [
        "SCALPING (5m)", "RANGE MODE (Rebond)", "DAY TRADING (1h)", "SWING (4h)"
    ])
    st.divider()
    st.subheader("🔧 Filtres scan")
    score_min  = st.slider("Score minimum",         20, 80, 40)
    nb_pairs   = st.slider("Paires à scanner",      10, 80, 50)
    debug_mode = st.checkbox("🔍 Mode debug", value=True)
    st.divider()
    st.caption(f"Positions actives : {len(st.session_state['test_positions'])}")
    if st.button("🔄 ACTUALISER TOUT"): st.rerun()
    if st.button("🗑️ RESET JOURNAL"):
        save_data(DB_FILE, {})
        st.session_state['test_positions'] = {}
        st.rerun()

# ============================================================
# ONGLET 1 : SCANNER
# ============================================================
with tab_scan:
    col_list, col_focus = st.columns([1, 2])

    with col_list:
        if st.button(f"LANCER SCAN {mode_actuel}"):
            all_results = []
            debug_log   = []
            err_types   = {}

            with st.status("Analyse des flux KuCoin...", expanded=True) as status:
                try:
                    ex           = get_exchange()
                    tickers_spot = ex.fetch_tickers()
                    pairs = sorted(
                        [s for s in tickers_spot
                         if s.endswith('/USDT')
                         and 'UP/'   not in s and 'DOWN/' not in s
                         and '3L/'   not in s and '3S/'   not in s],
                        key=lambda x: tickers_spot[x].get('quoteVolume') or 0,
                        reverse=True
                    )[:nb_pairs]

                    st.write(f"→ {len(pairs)} paires sélectionnées")
                    ok_n = fail_n = ret_n = 0

                    for p in pairs:
                        time.sleep(0.05)
                        ana, err = get_market_analysis(p, mode_actuel)
                        if err:
                            fail_n += 1
                            ek = err.split(':')[0]
                            err_types[ek] = err_types.get(ek, 0) + 1
                            debug_log.append(f"{p} → ❌ {err}")
                        else:
                            ok_n += 1
                            passes  = ana['score'] >= score_min
                            rsi_str = f"{ana['rsi']:.1f}" if ana['rsi'] else 'N/A'
                            ok_str  = '✅' if passes else '❌'
                            debug_log.append(f"{p} → score={ana['score']} adx={ana['adx']:.1f} rsi={rsi_str} {ok_str}")
                            if passes:
                                ret_n += 1
                                all_results.append({
                                    "sym":       p,
                                    "score":     ana['score'],
                                    "structure": ana['structure_htf'],
                                    "prix":      ana['prix'],
                                    "rsi":       ana['rsi'],
                                    "adx":       ana['adx'],
                                    "is_range":  ana['is_range'],
                                })

                    st.write(f"→ {ok_n} analysées · {fail_n} erreurs · {ret_n} retenus")
                    if err_types: st.write(f"→ Types erreurs : {err_types}")

                except Exception as e:
                    st.error(f"Erreur globale : {e}")
                    debug_log.append(traceback.format_exc())

                all_results.sort(key=lambda x: x['score'], reverse=True)
                st.session_state['scan_res']  = all_results
                st.session_state['debug_log'] = debug_log
                st.session_state['err_types'] = err_types
                status.update(label=f"✅ {len(all_results)} setup(s) trouvé(s)", state="complete")

        # Liste résultats — style V7.7
        if 'scan_res' in st.session_state:
            results   = st.session_state['scan_res']
            err_types = st.session_state.get('err_types', {})

            if results:
                for r in results:
                    trend = "🟢" if r['structure'] == "BULLISH" else "🔴" if r['structure'] == "BEARISH" else "🟡"
                    label = f"🎯 {trend} {r['sym']}  |  {r['score']}/100"
                    if st.button(label, key=f"btn_{r['sym']}"):
                        st.session_state['active_p'] = r['sym']
            else:
                st.warning("Aucun résultat. Baissez le score minimum (sidebar).")
                if err_types:
                    st.error(f"Erreurs : {err_types}")
                    if any(k in err_types for k in ['NetworkError', 'ExchangeNotAvailable']):
                        st.info("KuCoin bloqué depuis Streamlit Cloud. Remplacez par `ccxt.binance`.")

            if debug_mode and 'debug_log' in st.session_state:
                with st.expander("🔍 Debug — détail de chaque paire", expanded=False):
                    for line in st.session_state['debug_log']:
                        color = "#00FF41" if "✅" in line else "#FF4444" if "❌" in line else "#888"
                        st.markdown(
                            f"<span style='color:{color};font-size:11px;font-family:monospace'>{line}</span>",
                            unsafe_allow_html=True
                        )

    # --- FOCUS PAIRE (V7.7 Style avec Double Choix) ---
    with col_focus:
        if 'active_p' in st.session_state:
            p = st.session_state['active_p']
            res, err = get_market_analysis(p, mode_actuel)

            if err:
                st.error(f"Erreur analyse {p} : {err}")
            elif res:
                levels = compute_levels(res, mode_actuel)
                st.header(f"💼 Analyse : {p}")
                st.caption(f"Score : {res['score']}/100  |  HTF ({res['htf']}) : {res['structure_htf']}  |  LTF ({res['ltf']}) : {res['structure_ltf']}")

                st.subheader("📊 Paramètres avec Risque %")
                st_c1, st_c2, st_c3 = st.columns(3)
                
                # Affichage des niveaux avec les pourcentages comme avant
                st_c1.error(  f"SL: ({levels['risk_pct']:.2f}%) {levels['sl']:,.4f}")
                st_c2.warning(f"ENTREE OPTI: {levels['entry']:,.4f}")
                st_c3.success(f"TP: (+{levels['tp_pct']:.2f}%) {levels['tp']:,.4f}")
                
                st.caption(f"RR: {levels['rr']}  |  ADX: {res['adx']:.1f}  |  RSI: {res['rsi']:.1f}" if res['rsi'] else f"RR: {levels['rr']}  |  ADX: {res['adx']:.1f}")

                st.write("---")
                st.write("### 🛒 Mode d'entrée")
                btn_col1, btn_col2 = st.columns(2)

                # OPTION 1 : AU MARCHÉ (Prix actuel)
                if btn_col1.button(f"🚀 MARCHÉ (@{res['prix']:,.4f})"):
                    # Calcul du TP basé sur le prix actuel pour garder le même RR
                    current_risk_pct = ((levels['sl'] - res['prix']) / res['prix']) * 100
                    current_tp = res['prix'] + (abs(res['prix'] - levels['sl']) * levels['rr'])
                    current_tp_pct = ((current_tp - res['prix']) / res['prix']) * 100

                    st.session_state['test_positions'][p] = {
                        "symbol": p,
                        "entry": res['prix'],
                        "tp": current_tp,
                        "sl": levels['sl'],
                        "tp_pct": current_tp_pct,
                        "sl_pct": current_risk_pct,
                        "risk_pct": current_risk_pct,
                        "rr": levels['rr'],
                        "style": mode_actuel,
                        "score": res['score'],
                        "type_entree": "MARCHÉ",
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

                # OPTION 2 : LIMITE (Prix optimisé)
                if btn_col2.button(f"⏳ LIMITE (@{levels['entry']:,.4f})"):
                    st.session_state['test_positions'][p] = {
                        "symbol": p,
                        "entry": levels['entry'],
                        "tp": levels['tp'],
                        "sl": levels['sl'],
                        "tp_pct": levels['tp_pct'],
                        "sl_pct": levels['risk_pct'],
                        "risk_pct": levels['risk_pct'],
                        "rr": levels['rr'],
                        "style": mode_actuel,
                        "score": res['score'],
                        "type_entree": "LIMITE",
                        "time_full": datetime.now().strftime("%d/%m %H:%M:%S")
                    }
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

# ============================================================
# ONGLET 2 : RECHERCHE MANUELLE — style V7.7
# ============================================================
with tab_search:
    st.subheader("🔍 Analyser une paire spécifique (KuCoin)")
    pair_input = st.text_input("Ex: BTC, ETH, KCS", key="manual_search").upper().strip()

    if pair_input:
        analysis, err = get_market_analysis(pair_input, mode_actuel)

        if err:
            st.error(f"Erreur : {err}")
            st.info("Format attendu : BTC, ETH, SOL ou BTC/USDT")
        elif analysis:
            p_full = analysis['symbol']
            st.header(f"💼 Analyse : {p_full}")
            st.caption(f"Score : {analysis['score']}/100  |  HTF ({analysis['htf']}) : {analysis['structure_htf']}")

            levels = compute_levels(analysis, mode_actuel)

            m1, m2, m3 = st.columns(3)
            m1.metric("PRIX LIVE", f"{analysis['prix']:.6f}")
            m2.metric("ADX",       f"{analysis['adx']:.1f}", "Range" if analysis['is_range'] else "Trend")
            m3.metric("ATR",       f"{analysis['atr']:.6f}")

            st.write("---")
            sc1, sc2, sc3 = st.columns(3)
            sc1.error(  f"SL: ({levels['risk_pct']:.2f}%) {levels['sl']:,.4f}")
            sc2.warning(f"ENTRÉE: {levels['entry']:,.4f}")
            sc3.success(f"TP: (+{levels['tp_pct']:.2f}%) {levels['tp']:,.4f}")
            st.caption(f"RR: {levels['rr']}  |  ADX: {analysis['adx']:.1f}  |  RSI: {analysis['rsi']:.1f}" if analysis['rsi'] else f"RR: {levels['rr']}  |  ADX: {analysis['adx']:.1f}")

            if st.button(f"🚀 SURVEILLER {p_full}", key="btn_add_manual"):
                st.session_state['test_positions'][p_full] = {
                    "symbol":   p_full,
                    "entry":    levels['entry'],
                    "tp":       levels['tp'],
                    "sl":       levels['sl'],
                    "tp_pct":   levels['tp_pct'],
                    "sl_pct":   levels['risk_pct'],
                    "risk_pct": levels['risk_pct'],
                    "rr":       levels['rr'],
                    "style":    mode_actuel,
                    "score":    analysis['score'],
                    "time_full":datetime.now().strftime("%d/%m %H:%M:%S"),
                }
                save_data(DB_FILE, st.session_state['test_positions'])
                st.rerun()

# ============================================================
# ONGLET 3 : JOURNAL & PERFORMANCE — style V7.7
# ============================================================
with tab_journal:
    history = load_data(HIST_FILE)
    # On recharge les positions ouvertes directement depuis le fichier pour être sûr
    positions_actuelles = load_data(DB_FILE) 
    today = datetime.now().strftime("%d/%m")

    daily_pnl = 0.0
    for h in history:
        if h.get('FERMETURE', '').startswith(today):
            pnl_str = h.get('PNL %', '0').replace('+', '').replace('%', '').strip()
            try: daily_pnl += float(pnl_str)
            except: continue

    st.metric("PROFIT DU JOUR", f"{daily_pnl:+.2f}%", delta=f"{daily_pnl - 5:.2f}% vs Objectif 5%")
    st.progress(min(max(daily_pnl / 5, 0.0), 1.0), text=f"Progression : {daily_pnl:.2f}% / 5%")
    
    st.write("---")
    
    # --- AFFICHAGE DU CONTENU DE TRADING_JOURNAL.JSON ---
    st.subheader("📋 ORDRES DANS LE JOURNAL")
    if positions_actuelles:
        # On affiche ce qui est écrit dans le JSON sans attendre l'API
        df_open = pd.DataFrame.from_dict(positions_actuelles, orient='index')
        cols_brutes = ["symbol", "type_entree", "entry", "sl", "tp", "time_full"]
        cols_existantes = [c for c in cols_brutes if c in df_open.columns]
        st.dataframe(df_open[cols_existantes], use_container_width=True)
    else:
        st.info("Le fichier trading_journal.json est vide.")
    
    st.write("---")

    # --- SUIVI TEMPS RÉEL (Vérification des prix) ---
    if st.session_state['test_positions']:
        st.subheader("🎯 SUIVI LIVE (PRIX)")
        for p, data in list(st.session_state['test_positions'].items()):
            try:
                sym   = data.get('symbol', p)
                cur_p = get_exchange().fetch_ticker(sym)['last']
                prog  = ((cur_p - data['entry']) / data['entry']) * 100
                tp_check = data.get('tp', 9e15)

                if cur_p >= tp_check:
                    archive_position(sym, data, tp_check, "TP ✅")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()
                elif cur_p <= data['sl']:
                    archive_position(sym, data, data['sl'], "SL ❌")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

                with st.expander(f"📊 {sym} | {prog:+.2f}%", expanded=True):
                    c1, c2, c3 = st.columns([2, 2, 1])
                    c1.write(f"Ouvert à: {data.get('time_full', 'N/A')}")
                    c2.metric("PRIX LIVE", f"{cur_p:,.4f}", f"{prog:+.2f}%")
                    if c3.button("💰 VENDRE", key=f"sell_{p}"):
                        archive_position(sym, data, cur_p, "MANUEL")
                        del st.session_state['test_positions'][p]
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()
                    st.caption(
                        f"SL: {data['sl']:.4f}  |  "
                        f"TP: {data.get('tp', '?'):.4f}  |  "
                        f"Score: {data.get('score', 'N/A')}  |  "
                        f"Style: {data['style']}"
                    )
            except Exception as e:
                st.warning(f"Connexion API lente pour {p}... (Vérification en cours)")
                continue

    with st.expander("📜 HISTORIQUE DES TRADES CLOS", expanded=False):
        if history:
            df_hist = pd.DataFrame(history)
            cols_ordre = ["SYMBOLE", "OUVERTURE", "FERMETURE", "ENTREE", "SORTIE", "PNL %", "RR", "SCORE", "RAISON", "STYLE"]
            cols_ok = [c for c in cols_ordre if c in df_hist.columns]
            st.dataframe(df_hist[cols_ok].iloc[::-1], use_container_width=True)
