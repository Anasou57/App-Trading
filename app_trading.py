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
        "SYMBOLE":    symbol,
        "OUVERTURE":  data.get('time_full', "N/A"),
        "FERMETURE":  datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %":      f"{pnl:+.2f}%",
        "RAISON":     reason,
        "STYLE":      data['style'],
        "ENTREE":     f"{data['entry']:.4f}",
        "SORTIE":     f"{exit_price:.4f}",
        "RR":         data.get('rr', 'N/A'),
        "SCORE":      data.get('score', 'N/A'),
        "DIRECTION":  data.get('direction', 'LONG'),
        "CONF.":      data.get('confidence', 'N/A'),
        "pnl":        round(pnl, 2),
        "date":       datetime.now().strftime("%Y-%m-%d"),
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

@st.cache_data(ttl=60)
def test_connection():
    try:
        ex = ccxt.kucoin(SPOT_OPTS)
        t  = ex.fetch_ticker('BTC/USDT')
        return ('ok', t['last'])
    except Exception as e:
        return ('err', str(e))

# ============================================================
# COLLECTE DONNÉES
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

# ============================================================
# CALCUL INDICATEURS
# ============================================================
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

# ============================================================
# DÉTECTION STRUCTURE SMC
# ============================================================
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
            if gap > 0:   fvg = {'type':'bullish','low':float(df['h'].iloc[i-1]),'high':float(df['l'].iloc[i+1])}
            elif gap < 0: fvg = {'type':'bearish','low':float(df['l'].iloc[i+1]),'high':float(df['h'].iloc[i-1])}
    except: pass
    return ob_bull, ob_bear, fvg

# ============================================================
# CONTEXTE BTC — filtre macro
# ============================================================
@st.cache_data(ttl=120)
def get_btc_context():
    """
    Analyse BTC/USDT sur 1h pour qualifier le contexte macro.
    Retourne state: OK / CAUTION / DANGER
    """
    try:
        df  = compute_indicators(fetch_df("BTC/USDT", "1h"))
        row = df.iloc[-1]
        prev= df.iloc[-2]

        btc_rsi    = float(row.get('rsi') or 50)
        btc_ema20  = float(row.get('ema20') or 0)
        btc_ema50  = float(row.get('ema50') or 0)
        btc_close  = float(row['c'])
        var_1h     = ((btc_close - float(prev['c'])) / float(prev['c'])) * 100
        var_4h_row = df.iloc[-4] if len(df) >= 4 else prev
        var_4h     = ((btc_close - float(var_4h_row['c'])) / float(var_4h_row['c'])) * 100
        btc_trend  = "BULL" if btc_ema20 > btc_ema50 else "BEAR"
        macd_hist  = float(row.get('macd_hist') or 0)

        # DANGER : chute rapide ou BTC en tendance baissière forte
        if var_1h < -2.0 or var_4h < -4.0:
            return {"state": "DANGER",  "trend": btc_trend, "var_1h": var_1h, "var_4h": var_4h, "rsi": btc_rsi, "prix": btc_close}

        # CAUTION : BTC suracheté ou momentum faible
        if btc_rsi > 74 or (btc_trend == "BEAR" and macd_hist < 0):
            return {"state": "CAUTION", "trend": btc_trend, "var_1h": var_1h, "var_4h": var_4h, "rsi": btc_rsi, "prix": btc_close}

        return {"state": "OK", "trend": btc_trend, "var_1h": var_1h, "var_4h": var_4h, "rsi": btc_rsi, "prix": btc_close}

    except Exception as e:
        return {"state": "UNKNOWN", "trend": "?", "var_1h": 0, "var_4h": 0, "rsi": 50, "prix": 0}

# ============================================================
# DIRECTION EXPLICITE — LONG / SKIP
# ============================================================
def get_direction(res):
    """
    Détermine si le setup est LONG ou SKIP.
    On ne trade QUE LONG sur le spot.
    Retourne (direction, bull_score, bear_score)
    """
    bull = 0
    bear = 0

    c   = res.get('prix', 0)
    e20 = res.get('ema20')
    e50 = res.get('ema50')

    # EMA alignment (poids fort)
    if e20 and e50:
        if c > e20 > e50:  bull += 3
        elif c < e20 < e50: bear += 3

    # RSI
    rsi = res.get('rsi', 50) or 50
    if rsi < 45:   bull += 1
    if rsi < 30:   bull += 2
    if rsi > 55:   bear += 1
    if rsi > 70:   bear += 2

    # MACD histogramme
    mh = res.get('macd_hist', 0) or 0
    if mh > 0: bull += 2
    else:      bear += 2

    # Structure HTF (poids le plus fort)
    struct = res.get('structure_htf', 'NEUTRAL')
    if struct == "BULLISH": bull += 3
    if struct == "BEARISH": bear += 3

    # DMP vs DMN (force directionnelle ADX)
    dmp = res.get('dmp', 0) or 0
    dmn = res.get('dmn', 0) or 0
    if dmp > dmn: bull += 2
    else:         bear += 2

    # Stochastique
    stoch = res.get('stoch_k', 50) or 50
    if stoch < 30: bull += 1
    if stoch > 70: bear += 1

    # OB directionnel
    if res.get('ob_bull'): bull += 1
    if res.get('ob_bear'): bear += 1

    # On ne trade que LONG sur le spot
    if bull >= 8 and bull > bear + 2:
        return "LONG", bull, bear
    return "SKIP", bull, bear

# ============================================================
# CONFIRMATION DE BOUGIE FERMÉE
# ============================================================
def check_candle_confirmation(df_ltf):
    """
    Vérifie la dernière bougie FERMÉE (avant-dernière ligne).
    Retourne (confirmed: bool, message: str)
    """
    try:
        last_closed = df_ltf.iloc[-2]
        body  = abs(float(last_closed['c']) - float(last_closed['o']))
        rng   = float(last_closed['h']) - float(last_closed['l'])
        if rng == 0: return False, "Bougie plate"
        body_ratio  = body / rng
        is_bull     = float(last_closed['c']) > float(last_closed['o'])
        lower_wick  = float(last_closed['o']) - float(last_closed['l']) if is_bull else float(last_closed['c']) - float(last_closed['l'])

        if is_bull and body_ratio > 0.4:
            return True, f"Bougie haussière ({body_ratio*100:.0f}% corps)"
        if lower_wick > body * 2 and body_ratio > 0.1:
            return True, "Marteau détecté"
        return False, f"Bougie non confirmée ({body_ratio*100:.0f}% corps)"
    except:
        return False, "Erreur confirmation"

# ============================================================
# FILTRE VOLATILITÉ
# ============================================================
def check_volatility(res, mode):
    """
    ATR en % du prix — doit être dans la plage du style choisi.
    Trop calme = TP inatteignable. Trop fort = SL trop serré.
    """
    atr_pct = (res['atr'] / res['prix']) * 100
    ranges  = {
        "SCALPING (5m)":       (0.2, 3.5),
        "RANGE MODE (Rebond)": (0.15, 2.5),
        "DAY TRADING (1h)":    (0.5,  7.0),
        "SWING (4h)":          (1.5, 18.0),
    }
    low, high = ranges.get(mode, (0.1, 20.0))
    if atr_pct < low:  return False, f"Trop calme (ATR {atr_pct:.2f}% < {low}%)"
    if atr_pct > high: return False, f"Trop volatile (ATR {atr_pct:.2f}% > {high}%)"
    return True, f"Volatilité OK ({atr_pct:.2f}%)"

# ============================================================
# SCORE V2 — DIRECTIONNEL + FILTRES ÉLIMINATOIRES
# ============================================================
def score_v2(r_htf, r_ltf, mode, direction):
    """
    Score pondéré orienté LONG uniquement.
    Retourne 0 si la structure HTF est opposée (filtre éliminatoire).
    """
    # Filtre éliminatoire : jamais LONG contre structure BEARISH HTF
    if direction == "LONG" and r_htf.get('structure') == "BEARISH":
        return 0

    score = 0
    c   = r_ltf.get('c', 0) or 0
    e20 = r_ltf.get('ema20')
    e50 = r_ltf.get('ema50')
    e200= r_ltf.get('ema200')

    # 1. EMA alignment (25 pts)
    if e20 and e50 and e200:
        if c > e20 > e50 > e200: score += 25
        elif c > e20 > e50:      score += 18
        elif c > e50:            score += 10
    elif e20 and e50:
        if c > e20 > e50:        score += 18
        elif c > e50:            score += 8
    else:
        score += 5

    # 2. Structure HTF (20 pts)
    struct = r_htf.get('structure', 'NEUTRAL')
    if struct == "BULLISH":  score += 20
    elif struct == "NEUTRAL": score += 8
    # BEARISH = déjà éliminé ci-dessus

    # 3. MACD dans la direction (15 pts)
    mh = r_ltf.get('macd_hist', 0) or 0
    if mh > 0:  score += 15
    else:       score += 3   # contre MACD = risqué mais pas éliminatoire

    # 4. RSI zone favorable LONG (10 pts)
    rsi = r_ltf.get('rsi', 50) or 50
    if 35 < rsi < 60:  score += 10   # momentum propre
    elif rsi <= 35:    score += 8    # oversold = opportunité
    elif rsi < 70:     score += 4
    else:              score += 1   # suracheté = TP proche

    # 5. Volume confirme (10 pts)
    vr = r_ltf.get('vol_ratio', 1) or 1
    if vr > 1.5:   score += 10
    elif vr > 1.2: score += 6
    elif vr > 1.0: score += 3

    # 6. OB bullish présent (10 pts)
    if r_ltf.get('ob_bull'): score += 10
    elif r_ltf.get('ob_bear'): score += 2  # OB adverse = faible bonus

    # 7. FVG bullish (5 pts bonus)
    fvg = r_ltf.get('fvg')
    if fvg and fvg.get('type') == 'bullish': score += 5

    # 8. ADX confirme tendance (5 pts)
    adx = r_ltf.get('adx', 20) or 20
    if "RANGE" in mode and adx < 25:       score += 5
    elif "RANGE" not in mode and adx > 20: score += 5

    return min(score, 100)

# ============================================================
# NIVEAU DE CONFIANCE — détermine le TP cible
# ============================================================
def get_confidence_level(score, direction_bull, btc_state, candle_ok, vol_ok):
    """
    HAUTE  → score >= 75 + tous les filtres OK  → TP +3% à +4%
    MOYEN  → score >= 55                          → TP +2%
    NORMAL → score >= 40                          → TP +1.5%
    Retourne (level: str, tp_target_pct: float)
    """
    bonus = 0
    if btc_state == "OK":      bonus += 1
    if candle_ok:              bonus += 1
    if vol_ok:                 bonus += 1
    if direction_bull >= 10:   bonus += 1

    if score >= 75 and bonus >= 3:
        return "HAUTE",  3.5   # trade premium → laisser courir
    elif score >= 60 and bonus >= 2:
        return "MOYEN",  2.0   # trade standard → objectif 2%
    else:
        return "NORMAL", 1.5   # trade conservateur → 1.5% et sortir

# ============================================================
# CALCUL NIVEAUX — SL sur structure, TP selon confiance
# ============================================================
def compute_levels(res, mode_choisi, confidence_level="NORMAL", tp_target_pct=1.5):
    prix  = res['prix']   # prix actuel du marché
    atr   = res['atr'] or prix * 0.01

    # ── ENTRÉE ──
    if "RANGE" in mode_choisi and res.get('bb_lower'):
        entry = res['ob_bull'] if res['ob_bull'] else res['bb_lower']
    elif res.get('structure_htf') == "BULLISH" and res.get('ema20'):
        entry = min(prix, res['ema20'])
    else:
        entry = prix
    if not entry: entry = prix

    # ── STOP LOSS sur structure (swing low) ──
    sl_structure = None
    try:
        recent_lows = [res.get('bb_lower'), res.get('ob_bull')]
        valid = [v for v in recent_lows if v and v < entry]
        if valid:
            sl_structure = min(valid) - atr * 0.3
    except: pass

    sl_atr = entry - atr * 1.5
    if sl_structure:
        sl = min(sl_atr, sl_structure)
    elif res.get('bb_lower'):
        sl = min(sl_atr, res['bb_lower'] * 0.998)
    else:
        sl = sl_atr

    # ── RÈGLE SL : minimum 1.2%, maximum 3% ──
    # Minimum : jamais moins de 1.2% sous l'entrée
    #   → protège contre les faux SL trop serrés qui se font toucher par le bruit
    # Maximum : jamais plus de 3% (protection du capital, max 1.2%×4 = ~5% de perte jour)
    sl = min(sl, entry * 0.988)   # max -1.2% : si SL calculé trop serré → on l'élargit
    sl = max(sl, entry * 0.970)   # max -3%   : si SL calculé trop loin → on le resserre

    risk = entry - sl
    if risk <= 0: risk = entry * 0.012   # fallback 1.2%

    # ── TP selon niveau de confiance ──
    tp_from_rr  = entry + risk * 2.0
    tp_from_pct = entry * (1 + tp_target_pct / 100)

    if "RANGE" in mode_choisi and res.get('bb_upper'):
        tp_range = res['bb_upper'] * 0.995
        tp = max(tp_range, tp_from_pct)
    else:
        tp = max(tp_from_rr, tp_from_pct)

    risk_final = max(entry - sl, entry * 0.012)
    rr         = round(abs(tp - entry) / risk_final, 2)

    return {
        "prix_actuel": prix,                                    # prix live au moment de l'analyse
        "entry":       entry,
        "sl":          sl,
        "tp":          tp,
        "tp_pct":      ((tp - entry) / entry) * 100,
        "risk_pct":    ((sl - entry) / entry) * 100,           # négatif ex: -1.5%
        "rr":          rr,
        "confidence":  confidence_level,
    }

# ============================================================
# ANALYSE COMPLÈTE D'UNE PAIRE
# ============================================================
def get_market_analysis(symbol, mode_choisi, df_ltf_out=None):
    """
    Retourne (result_dict, error_str, df_ltf).
    df_ltf exposé pour la confirmation de bougie.
    """
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

        adx_val = float(row_ltf.get('adx') or 20)

        def fv(k): return float(row_ltf[k]) if row_ltf.get(k) is not None else None

        res = {
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
            "dmp":           fv('dmp'),
            "dmn":           fv('dmn'),
            "structure_htf": row_htf['structure'],
            "structure_ltf": detect_structure(df_ltf),
            "ob_bull":       ob_bull,
            "ob_bear":       ob_bear,
            "fvg":           fvg,
            "is_range":      adx_val < 22,
            "htf":           htf,
            "ltf":           ltf,
        }

        # Direction
        direction, bull_pts, bear_pts = get_direction(res)
        res['direction']  = direction
        res['bull_pts']   = bull_pts
        res['bear_pts']   = bear_pts

        # Score V2 directionnel
        res['score'] = score_v2(row_htf, row_ltf, mode_choisi, direction)

        return res, None, df_ltf

    except Exception as e:
        return None, f"{type(e).__name__}: {str(e)}", None

# ============================================================
# STYLE V7.7
# ============================================================
st.set_page_config(page_title="SMC V9 — SPOT KUCOIN", layout="wide")

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
            if cur_p >= data['tp']:
                archive_position(sym, data, data['tp'], "TP ✅")
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
st.title("📟 SMC PERFORMANCE V9")

cx = test_connection()
if cx[0] == 'ok':
    st.success(f"✅ Connexion KuCoin OK | BTC: {cx[1]:,.2f}$")
else:
    st.error(f"❌ Erreur connexion : {cx[1]}")

# ── Contexte BTC (affiché en permanence) ──
btc_ctx = get_btc_context()
btc_color = {"OK": "🟢", "CAUTION": "🟡", "DANGER": "🔴", "UNKNOWN": "⚪"}
btc_icon  = btc_color.get(btc_ctx['state'], "⚪")
btc_msg   = (f"{btc_icon} BTC Context : **{btc_ctx['state']}** | "
             f"Trend: {btc_ctx['trend']} | "
             f"RSI: {btc_ctx['rsi']:.0f} | "
             f"Δ1h: {btc_ctx['var_1h']:+.2f}% | "
             f"Δ4h: {btc_ctx['var_4h']:+.2f}%")

if btc_ctx['state'] == "DANGER":
    st.error(btc_msg + " — ⛔ LONG sur alts déconseillé")
elif btc_ctx['state'] == "CAUTION":
    st.warning(btc_msg + " — ⚠️ Réduire la taille des positions")
else:
    st.info(btc_msg)

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

    st.subheader("🎯 Stratégie 5%/jour")
    st.caption("Objectif : 4 trades × 1.5-2% = 6-8%")
    trades_done = 0
    daily_done  = 0.0
    history_now = load_data(HIST_FILE)
    today_str   = datetime.now().strftime("%Y-%m-%d")
    for h in history_now:
        if h.get('date') == today_str:
            trades_done += 1
            try: daily_done += float(h.get('pnl', 0))
            except: pass
    st.metric("Trades aujourd'hui", f"{trades_done}/4", delta=f"{daily_done:+.2f}% réalisé")
    trades_restants = max(0, 4 - trades_done)
    pnl_restant     = max(0, 5.0 - daily_done)
    if trades_restants > 0:
        st.caption(f"→ Il faut encore {pnl_restant:.1f}% sur {trades_restants} trade(s)")
    else:
        st.success("🎯 Objectif du jour atteint !")

    st.divider()
    st.subheader("🔧 Filtres scan")
    score_min  = st.slider("Score minimum",      30, 80, 50)
    nb_pairs   = st.slider("Paires à scanner",   10, 80, 50)
    debug_mode = st.checkbox("🔍 Mode debug",    value=False)
    skip_caution = st.checkbox("⛔ Bloquer si BTC DANGER", value=True)
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

        # Alerte BTC avant le scan
        if btc_ctx['state'] == "DANGER" and skip_caution:
            st.error("⛔ Scan bloqué — BTC en danger.\nDésactivez le filtre dans la sidebar si vous voulez quand même scanner.")
        else:
            if btc_ctx['state'] == "DANGER":
                st.warning("⚠️ BTC en danger — résultats à prendre avec précaution")

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
                        ok_n = fail_n = ret_n = skip_n = 0

                        for p in pairs:
                            time.sleep(0.05)
                            res, err, df_ltf = get_market_analysis(p, mode_actuel)

                            if err:
                                fail_n += 1
                                ek = err.split(':')[0]
                                err_types[ek] = err_types.get(ek, 0) + 1
                                debug_log.append(f"{p} → ❌ {err}")
                                continue

                            ok_n += 1

                            # Filtre direction
                            if res['direction'] == "SKIP":
                                skip_n += 1
                                debug_log.append(f"{p} → ⏭️ SKIP (bull={res['bull_pts']} bear={res['bear_pts']})")
                                continue

                            # Filtre score
                            if res['score'] < score_min:
                                debug_log.append(f"{p} → ❌ score={res['score']} < {score_min}")
                                continue

                            # Filtre volatilité
                            vol_ok, vol_msg = check_volatility(res, mode_actuel)
                            if not vol_ok:
                                debug_log.append(f"{p} → ❌ {vol_msg}")
                                continue

                            # Filtre BTC CAUTION → on accepte mais on note
                            btc_warn = btc_ctx['state'] != "OK"

                            # Confirmation bougie
                            candle_ok, candle_msg = check_candle_confirmation(df_ltf)

                            # Niveau de confiance
                            conf_level, tp_pct = get_confidence_level(
                                res['score'], res['bull_pts'],
                                btc_ctx['state'], candle_ok, vol_ok
                            )

                            ret_n += 1
                            rsi_str = f"{res['rsi']:.1f}" if res['rsi'] else 'N/A'
                            debug_log.append(
                                f"{p} → ✅ score={res['score']} bull={res['bull_pts']} "
                                f"rsi={rsi_str} conf={conf_level} tp_cible={tp_pct}%"
                            )
                            all_results.append({
                                "sym":         p,
                                "score":       res['score'],
                                "structure":   res['structure_htf'],
                                "direction":   res['direction'],
                                "bull_pts":    res['bull_pts'],
                                "rsi":         res['rsi'],
                                "adx":         res['adx'],
                                "is_range":    res['is_range'],
                                "confidence":  conf_level,
                                "tp_pct":      tp_pct,
                                "vol_msg":     vol_msg,
                                "candle_ok":   candle_ok,
                                "candle_msg":  candle_msg,
                                "btc_warn":    btc_warn,
                            })

                        st.write(f"→ {ok_n} analysées · {skip_n} SKIP direction · {fail_n} erreurs · {ret_n} retenus")
                        if err_types: st.write(f"→ Types erreurs : {err_types}")

                    except Exception as e:
                        st.error(f"Erreur globale : {e}")
                        debug_log.append(traceback.format_exc())

                    # Tri : HAUTE confiance d'abord, puis par score
                    conf_order = {"HAUTE": 0, "MOYEN": 1, "NORMAL": 2}
                    all_results.sort(key=lambda x: (conf_order.get(x['confidence'], 3), -x['score']))

                    st.session_state['scan_res']  = all_results
                    st.session_state['debug_log'] = debug_log
                    st.session_state['err_types'] = err_types
                    status.update(label=f"✅ {len(all_results)} setup(s) LONG valides", state="complete")

        # ── Liste résultats ──
        if 'scan_res' in st.session_state:
            results   = st.session_state['scan_res']
            err_types = st.session_state.get('err_types', {})

            if results:
                for r in results:
                    conf_icon = "🔥" if r['confidence'] == "HAUTE" else "✅" if r['confidence'] == "MOYEN" else "📌"
                    warn_icon = "⚠️" if r.get('btc_warn') else ""
                    candle_icon = "🕯️" if r.get('candle_ok') else ""
                    label = (f"🎯 {conf_icon} {r['sym']}  |  {r['score']}/100  "
                             f"|  TP cible: +{r['tp_pct']}%  {warn_icon}{candle_icon}")
                    if st.button(label, key=f"btn_{r['sym']}"):
                        st.session_state['active_p']   = r['sym']
                        st.session_state['active_conf']= r['confidence']
                        st.session_state['active_tp']  = r['tp_pct']
            else:
                st.warning("Aucun setup LONG valide trouvé.")
                if st.session_state.get('err_types'):
                    st.error(f"Erreurs : {st.session_state['err_types']}")

            if debug_mode and 'debug_log' in st.session_state:
                with st.expander("🔍 Debug", expanded=False):
                    for line in st.session_state['debug_log']:
                        color = "#00FF41" if "✅" in line else "#FF4444" if "❌" in line else "#FFB800" if "⏭️" in line else "#888"
                        st.markdown(
                            f"<span style='color:{color};font-size:11px;font-family:monospace'>{line}</span>",
                            unsafe_allow_html=True
                        )

    # ── Focus paire ──
    with col_focus:
        if 'active_p' in st.session_state:
            p          = st.session_state['active_p']
            conf_level = st.session_state.get('active_conf', 'NORMAL')
            tp_target  = st.session_state.get('active_tp',  1.5)

            res, err, df_ltf = get_market_analysis(p, mode_actuel)

            if err:
                st.error(f"Erreur analyse {p} : {err}")
            elif res:
                if res['direction'] == "SKIP":
                    st.warning(f"⏭️ Setup SKIP — signal directionnel insuffisant (bull={res['bull_pts']} bear={res['bear_pts']})")
                else:
                    candle_ok, candle_msg = check_candle_confirmation(df_ltf)
                    vol_ok, vol_msg       = check_volatility(res, mode_actuel)
                    conf_level, tp_target = get_confidence_level(
                        res['score'], res['bull_pts'], btc_ctx['state'], candle_ok, vol_ok
                    )
                    levels = compute_levels(res, mode_actuel, conf_level, tp_target)

                    # ── Header ──
                    conf_badge = {"HAUTE": "🔥 HAUTE", "MOYEN": "✅ MOYEN", "NORMAL": "📌 NORMAL"}
                    st.header(f"💼 Analyse : {p}")
                    st.caption(
                        f"Score : {res['score']}/100  |  Confiance : {conf_badge.get(conf_level)}  |  "
                        f"HTF ({res['htf']}) : {res['structure_htf']}  |  "
                        f"LTF ({res['ltf']}) : {res['structure_ltf']}"
                    )

                    # ── Filtres appliqués ──
                    f1, f2, f3 = st.columns(3)
                    f1.info(f"{'✅' if candle_ok else '⚠️'} Bougie : {candle_msg}")
                    f2.info(f"{'✅' if vol_ok else '⚠️'} Vol : {vol_msg}")
                    f3.info(f"{btc_icon} BTC : {btc_ctx['state']}")

                    # ── Niveaux style V7.7 + prix actuel ──
                    st.subheader("📊 Paramètres avec Risque %")
                    st_c1, st_c2, st_c3, st_c4 = st.columns(4)
                    st_c1.info(   f"PRIX ACTUEL\n{levels['prix_actuel']:,.4f}")
                    st_c2.error(  f"SL: ({levels['risk_pct']:.2f}%) {levels['sl']:,.4f}")
                    st_c3.warning(f"ENTREE: {levels['entry']:,.4f}")
                    st_c4.success(f"TP: (+{levels['tp_pct']:.2f}%) {levels['tp']:,.4f}")
                    st.caption(
                        f"RR: {levels['rr']}  |  "
                        f"ADX: {res['adx']:.1f}  |  "
                        f"RSI: {res['rsi']:.1f}" if res['rsi'] else
                        f"RR: {levels['rr']}  |  ADX: {res['adx']:.1f}"
                    )

                    # ── Badge confiance ──
                    if conf_level == "HAUTE":
                        st.success(f"🔥 Setup PREMIUM — TP cible +{tp_target}% — Laisser courir")
                    elif conf_level == "MOYEN":
                        st.warning(f"✅ Setup STANDARD — TP cible +{tp_target}% — Sortie rapide")
                    else:
                        st.info(f"📌 Setup CONSERVATEUR — TP cible +{tp_target}% — Sortie dès atteint")

                    if st.button("🚀 VALIDER ET SURVEILLER"):
                        st.session_state['test_positions'][p] = {
                            "symbol":     p,
                            "entry":      levels['entry'],
                            "tp":         levels['tp'],
                            "sl":         levels['sl'],
                            "tp_pct":     levels['tp_pct'],
                            "risk_pct":   levels['risk_pct'],
                            "rr":         levels['rr'],
                            "style":      mode_actuel,
                            "score":      res['score'],
                            "direction":  res['direction'],
                            "confidence": conf_level,
                            "time_full":  datetime.now().strftime("%d/%m %H:%M:%S"),
                        }
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()

# ============================================================
# ONGLET 2 : RECHERCHE MANUELLE
# ============================================================
with tab_search:
    st.subheader("🔍 Analyser une paire spécifique (KuCoin Spot)")
    pair_input = st.text_input("Ex: BTC, ETH, SOL", key="manual_search").upper().strip()

    if pair_input:
        res, err, df_ltf = get_market_analysis(pair_input, mode_actuel)

        if err:
            st.error(f"Erreur : {err}")
            st.info("Format : BTC, ETH ou BTC/USDT")
        elif res:
            candle_ok, candle_msg = check_candle_confirmation(df_ltf) if df_ltf is not None else (False, "N/A")
            vol_ok,    vol_msg    = check_volatility(res, mode_actuel)
            conf_level, tp_target = get_confidence_level(
                res['score'], res['bull_pts'], btc_ctx['state'], candle_ok, vol_ok
            )
            levels = compute_levels(res, mode_actuel, conf_level, tp_target)

            p_full = res['symbol']
            conf_badge = {"HAUTE": "🔥 HAUTE", "MOYEN": "✅ MOYEN", "NORMAL": "📌 NORMAL"}
            st.header(f"💼 Analyse : {p_full}")
            st.caption(f"Score : {res['score']}/100  |  Confiance : {conf_badge.get(conf_level)}  |  Direction : {res['direction']}")

            m1, m2, m3 = st.columns(3)
            m1.metric("PRIX LIVE", f"{res['prix']:.6f}")
            m2.metric("ADX",       f"{res['adx']:.1f}", "Range" if res['is_range'] else "Trend")
            m3.metric("ATR",       f"{res['atr']:.6f}")

            st.write("---")
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.info(   f"PRIX ACTUEL\n{levels['prix_actuel']:,.4f}")
            sc2.error(  f"SL: ({levels['risk_pct']:.2f}%) {levels['sl']:,.4f}")
            sc3.warning(f"ENTRÉE: {levels['entry']:,.4f}")
            sc4.success(f"TP: (+{levels['tp_pct']:.2f}%) {levels['tp']:,.4f}")
            st.caption(
                f"RR: {levels['rr']}  |  Bougie: {candle_msg}  |  {vol_msg}  |  BTC: {btc_ctx['state']}"
            )

            if res['direction'] == "SKIP":
                st.warning(f"⏭️ Direction SKIP — bull={res['bull_pts']} bear={res['bear_pts']} — setup non recommandé")

            if st.button(f"🚀 SURVEILLER {p_full}", key="btn_add_manual"):
                st.session_state['test_positions'][p_full] = {
                    "symbol":     p_full,
                    "entry":      levels['entry'],
                    "tp":         levels['tp'],
                    "sl":         levels['sl'],
                    "tp_pct":     levels['tp_pct'],
                    "risk_pct":   levels['risk_pct'],
                    "rr":         levels['rr'],
                    "style":      mode_actuel,
                    "score":      res['score'],
                    "direction":  res['direction'],
                    "confidence": conf_level,
                    "time_full":  datetime.now().strftime("%d/%m %H:%M:%S"),
                }
                save_data(DB_FILE, st.session_state['test_positions'])
                st.rerun()

# ============================================================
# ONGLET 3 : JOURNAL & PERFORMANCE
# ============================================================
with tab_journal:
    history = load_data(HIST_FILE)
    today   = datetime.now().strftime("%d/%m")
    today_y = datetime.now().strftime("%Y-%m-%d")

    daily_pnl   = 0.0
    wins        = 0
    losses      = 0
    trades_jour = 0

    for h in history:
        if h.get('FERMETURE', '').startswith(today):
            trades_jour += 1
            try:
                val = float(h.get('PNL %','0').replace('+','').replace('%','').strip())
                daily_pnl += val
                if val > 0: wins += 1
                else:       losses += 1
            except: continue

    win_rate = (wins / (wins+losses) * 100) if (wins+losses) > 0 else 0

    # Barre objectif 5%
    st.subheader("📊 Objectif du jour — 5% en 4 trades")
    j1, j2, j3, j4 = st.columns(4)
    j1.metric("PROFIT DU JOUR",  f"{daily_pnl:+.2f}%", f"{daily_pnl-5:.2f}% vs obj 5%")
    j2.metric("TRADES (jour)",   f"{trades_jour}/4")
    j3.metric("WIN RATE",        f"{win_rate:.0f}%",    f"{wins}W/{losses}L")
    j4.metric("RESTANT",         f"{max(0,5-daily_pnl):.2f}%", f"sur {max(0,4-trades_jour)} trade(s)")

    st.progress(min(max(daily_pnl / 5, 0.0), 1.0), text=f"Progression : {daily_pnl:.2f}% / 5%")
    st.write("---")

    # Positions actives
    if st.session_state['test_positions']:
        for p, data in list(st.session_state['test_positions'].items()):
            try:
                sym   = data.get('symbol', p)
                cur_p = get_exchange().fetch_ticker(sym)['last']
                prog  = ((cur_p - data['entry']) / data['entry']) * 100

                if cur_p >= data['tp']:
                    archive_position(sym, data, data['tp'], "TP ✅")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()
                elif cur_p <= data['sl']:
                    archive_position(sym, data, data['sl'], "SL ❌")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

                conf = data.get('confidence', 'NORMAL')
                conf_icon = "🔥" if conf == "HAUTE" else "✅" if conf == "MOYEN" else "📌"

                with st.expander(f"📊 {conf_icon} {sym} | P&L live: {prog:+.2f}%", expanded=True):
                    c1, c2 = st.columns([3, 1])
                    c1.metric("PRIX ACTUEL", f"{cur_p:,.4f}", f"{prog:+.2f}%")
                    if c2.button("💰 VENDRE", key=f"sell_{p}"):
                        pnl_manuel = ((cur_p - data['entry']) / data['entry']) * 100
                        raison     = "MANUEL ✅" if pnl_manuel >= 0 else "MANUEL ❌"
                        archive_position(sym, data, cur_p, raison)
                        del st.session_state['test_positions'][p]
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()
                    st.caption(
                        f"📅 {data.get('time_full', 'N/A')}  |  "
                        f"Entrée: {data['entry']:,.4f}  |  "
                        f"TP: {data['tp']:,.4f} (+{data.get('tp_pct',0):.2f}%)  |  "
                        f"SL: {data['sl']:,.4f} ({data.get('risk_pct',0):.2f}%)  |  "
                        f"RR: {data.get('rr','N/A')}  |  Score: {data.get('score','N/A')}"
                    )
            except Exception as e:
                st.warning(f"Erreur {p}: {e}")

    else:
        st.info("Aucune position active.")

    st.write("---")
    with st.expander("📜 VOIR L'HISTORIQUE DÉTAILLÉ", expanded=False):
        if history:
            df_hist    = pd.DataFrame(history)
            cols_ordre = ["SYMBOLE","OUVERTURE","FERMETURE","ENTREE","SORTIE",
                          "PNL %","RR","SCORE","CONF.","DIRECTION","RAISON","STYLE"]
            cols_ok    = [c for c in cols_ordre if c in df_hist.columns]
            st.dataframe(df_hist[cols_ok].iloc[::-1], use_container_width=True)

            # Courbe PnL cumulé
            vals = []
            for h in history:
                try: vals.append(float(h.get('PNL %','0').replace('+','').replace('%','').strip()))
                except: pass
            if vals:
                cumul = pd.Series(vals).cumsum()
                st.line_chart(pd.DataFrame({"PnL cumulé (%)": cumul}))
        else:
            st.info("Historique vide.")
