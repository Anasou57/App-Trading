import streamlit as st
import ccxt
import pandas as pd
import pandas_ta as ta
import json
import os
import time
from datetime import datetime

# ============================================================
# PERSISTANCE
# ============================================================
DB_FILE   = "trading_journal.json"
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
        "SYMBOLE":    symbol,
        "MARCHÉ":     data.get('market_type', 'SPOT'),
        "OUVERTURE":  data.get('time_full', "N/A"),
        "FERMETURE":  datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %":      f"{pnl:+.2f}%",
        "RAISON":     reason,
        "STYLE":      data['style'],
        "ENTREE":     f"{data['entry']:.6f}",
        "SORTIE":     f"{exit_price:.6f}",
        "RR":         data.get('rr', 'N/A'),
        "SCORE":      data.get('score', 'N/A'),
        "pnl":        round(pnl, 2),
        "date":       datetime.now().strftime("%Y-%m-%d"),
    })
    save_data(HIST_FILE, history)
    return pnl

# ============================================================
# EXCHANGES
# ============================================================
@st.cache_resource
def get_spot_exchange():
    return ccxt.kucoin({'timeout': 30000, 'enableRateLimit': True})

@st.cache_resource
def get_futures_exchange():
    return ccxt.kucoinfutures({'timeout': 30000, 'enableRateLimit': True})

# ============================================================
# CONFIGURATION
# ============================================================
st.set_page_config(page_title="SMC V8 PRO — SPOT & FUTURES", layout="wide")

if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)
if 'last_monitor' not in st.session_state:
    st.session_state['last_monitor'] = 0

exchange_spot    = get_spot_exchange()
exchange_futures = get_futures_exchange()

# ============================================================
# MOTEUR D'ANALYSE SMC — MULTI-TIMEFRAME
# ============================================================

def fetch_df(exchange_obj, symbol, tf, limit=150):
    bars = exchange_obj.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df   = pd.DataFrame(bars, columns=['t', 'o', 'h', 'l', 'c', 'v'])
    return df

def compute_indicators(df):
    df['ema20']  = ta.ema(df['c'], length=20)
    df['ema50']  = ta.ema(df['c'], length=50)
    df['ema200'] = ta.ema(df['c'], length=200)
    df['rsi']    = ta.rsi(df['c'], length=14)

    macd = ta.macd(df['c'], fast=12, slow=26, signal=9)
    if macd is not None:
        df['macd']        = macd['MACD_12_26_9']
        df['macd_signal'] = macd['MACDs_12_26_9']
        df['macd_hist']   = macd['MACDh_12_26_9']

    df['atr'] = ta.atr(df['h'], df['l'], df['c'], length=14)

    bb = ta.bbands(df['c'], length=20, std=2)
    if bb is not None:
        df['bb_upper'] = bb['BBU_20_2.0']
        df['bb_lower'] = bb['BBL_20_2.0']
        df['bb_mid']   = bb['BBM_20_2.0']
        df['bb_width'] = (df['bb_upper'] - df['bb_lower']) / df['bb_mid']

    adx_data = ta.adx(df['h'], df['l'], df['c'], length=14)
    if adx_data is not None:
        df['adx'] = adx_data['ADX_14']
        df['dmp'] = adx_data['DMP_14']
        df['dmn'] = adx_data['DMN_14']

    df['vol_sma20'] = df['v'].rolling(20).mean()
    df['vol_ratio'] = df['v'] / df['vol_sma20']

    stoch = ta.stoch(df['h'], df['l'], df['c'], k=14, d=3)
    if stoch is not None:
        df['stoch_k'] = stoch['STOCHk_14_3_3']
        df['stoch_d'] = stoch['STOCHd_14_3_3']

    return df

def detect_structure(df):
    highs = df['h'].rolling(5, center=True).max()
    lows  = df['l'].rolling(5, center=True).min()
    recent_highs = [df['h'].iloc[i] for i in range(len(df)-20, len(df)) if df['h'].iloc[i] == highs.iloc[i]]
    recent_lows  = [df['l'].iloc[i] for i in range(len(df)-20, len(df)) if df['l'].iloc[i] == lows.iloc[i]]
    if len(recent_highs) >= 2 and len(recent_lows) >= 2:
        if recent_highs[-1] > recent_highs[-2] and recent_lows[-1] > recent_lows[-2]:
            return "BULLISH"
        elif recent_highs[-1] < recent_highs[-2] and recent_lows[-1] < recent_lows[-2]:
            return "BEARISH"
    return "NEUTRAL"

def detect_ob_fvg(df):
    ob_bull, ob_bear, fvg = None, None, None
    for i in range(len(df)-10, len(df)-1):
        if df['c'].iloc[i] < df['o'].iloc[i] and df['c'].iloc[i+1] > df['o'].iloc[i+1]:
            if df['c'].iloc[i+1] > df['h'].iloc[i]:
                ob_bull = df['l'].iloc[i]
        if df['c'].iloc[i] > df['o'].iloc[i] and df['c'].iloc[i+1] < df['o'].iloc[i+1]:
            if df['c'].iloc[i+1] < df['l'].iloc[i]:
                ob_bear = df['h'].iloc[i]
    for i in range(len(df)-8, len(df)-2):
        gap = df['l'].iloc[i+1] - df['h'].iloc[i-1]
        if gap > 0:
            fvg = {'type': 'bullish', 'low': df['h'].iloc[i-1], 'high': df['l'].iloc[i+1]}
        elif gap < 0:
            fvg = {'type': 'bearish', 'low': df['l'].iloc[i+1], 'high': df['h'].iloc[i-1]}
    return ob_bull, ob_bear, fvg

def score_setup(r_htf, r_ltf, mode):
    score = 0
    last  = r_ltf

    if last['ema20'] and last['ema50'] and last['ema200']:
        if last['c'] > last['ema20'] > last['ema50'] > last['ema200']:   score += 20
        elif last['c'] < last['ema20'] < last['ema50'] < last['ema200']: score += 20
        elif last['c'] > last['ema50']:                                  score += 10

    if last['rsi']:
        if 40 < last['rsi'] < 65:              score += 15
        elif last['rsi'] < 35 or last['rsi'] > 70: score += 8

    if last.get('macd') and last.get('macd_signal'):
        if last['macd'] > last['macd_signal'] and last.get('macd_hist', 0) > 0: score += 15
        elif last['macd'] < last['macd_signal']:                                 score += 5

    if last.get('vol_ratio', 1) > 1.3: score += 10

    if r_htf['structure'] != "NEUTRAL":
        if r_htf['structure'] == "BULLISH" and last['c'] > last.get('ema50', 0):        score += 20
        elif r_htf['structure'] == "BEARISH" and last['c'] < last.get('ema50', 999999): score += 20
        else:                                                                             score += 5

    if r_ltf.get('ob_bull') or r_ltf.get('ob_bear'): score += 10

    adx = last.get('adx', 20)
    if "RANGE" in mode and adx < 22:      score += 10
    elif "RANGE" not in mode and adx > 25: score += 10

    return min(score, 100)

def get_market_analysis(exchange_obj, symbol, mode_choisi, market_type="SPOT"):
    """Analyse multi-timeframe complète — fonctionne pour Spot et Futures."""
    try:
        if "SCALPING" in mode_choisi:
            htf, ltf = "15m", "5m"
        elif "RANGE" in mode_choisi:
            htf, ltf = "1h",  "15m"
        elif "DAY" in mode_choisi:
            htf, ltf = "4h",  "1h"
        else:
            htf, ltf = "1d",  "4h"

        df_htf = compute_indicators(fetch_df(exchange_obj, symbol, htf))
        df_ltf = compute_indicators(fetch_df(exchange_obj, symbol, ltf))

        row_htf = df_htf.iloc[-1].to_dict()
        row_ltf = df_ltf.iloc[-1].to_dict()

        row_htf['structure'] = detect_structure(df_htf)
        ob_bull, ob_bear, fvg = detect_ob_fvg(df_ltf)
        row_ltf['ob_bull'] = ob_bull
        row_ltf['ob_bear'] = ob_bear
        row_ltf['fvg']     = fvg

        score    = score_setup(row_htf, row_ltf, mode_choisi)
        is_range = (row_ltf.get('adx') or 25) < 22

        return {
            "symbol":        symbol,
            "market_type":   market_type,
            "prix":          row_ltf['c'],
            "atr":           row_ltf['atr'],
            "adx":           row_ltf.get('adx'),
            "rsi":           row_ltf.get('rsi'),
            "macd_hist":     row_ltf.get('macd_hist'),
            "ema20":         row_ltf.get('ema20'),
            "ema50":         row_ltf.get('ema50'),
            "ema200":        row_ltf.get('ema200'),
            "bb_upper":      row_ltf.get('bb_upper'),
            "bb_lower":      row_ltf.get('bb_lower'),
            "bb_width":      row_ltf.get('bb_width'),
            "vol_ratio":     row_ltf.get('vol_ratio', 1),
            "stoch_k":       row_ltf.get('stoch_k'),
            "structure_htf": row_htf['structure'],
            "structure_ltf": detect_structure(df_ltf),
            "ob_bull":       ob_bull,
            "ob_bear":       ob_bear,
            "fvg":           row_ltf.get('fvg'),
            "is_range":      is_range,
            "score":         score,
            "htf":           htf,
            "ltf":           ltf,
        }
    except:
        return None

def compute_levels(res, mode_choisi):
    prix = res['prix']
    atr  = res['atr'] or prix * 0.01

    if "RANGE" in mode_choisi:
        entry = res['ob_bull'] if res['ob_bull'] else res['bb_lower']
    elif res['structure_htf'] == "BULLISH":
        entry = min(prix, res['ema20']) if res['ema20'] else prix
    else:
        entry = prix

    if entry is None:
        entry = prix

    sl_atr = entry - atr * 1.5
    if res['ob_bull']:
        sl = min(sl_atr, res['ob_bull'] - atr * 0.3)
    elif res['bb_lower']:
        sl = min(sl_atr, res['bb_lower'] * 0.998)
    else:
        sl = sl_atr

    risk = entry - sl
    if risk <= 0:
        risk = atr * 1.5

    tp1 = entry + risk * 1.5
    tp2 = entry + risk * 2.5
    tp3 = entry + risk * 4.0

    if "RANGE" in mode_choisi and res['bb_upper']:
        tp1 = res['bb_upper'] * 0.995

    return {
        "entry":    entry,
        "sl":       sl,
        "tp1":      tp1,
        "tp2":      tp2,
        "tp3":      tp3,
        "risk_pct": ((sl - entry) / entry) * 100,
        "tp1_pct":  ((tp1 - entry) / entry) * 100,
        "tp2_pct":  ((tp2 - entry) / entry) * 100,
        "tp3_pct":  ((tp3 - entry) / entry) * 100,
        "rr_tp1":   round(abs(tp1 - entry) / abs(risk), 2),
        "rr_tp2":   round(abs(tp2 - entry) / abs(risk), 2),
    }

# ============================================================
# STYLE
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
# AUTO-MONITORING (60s)
# ============================================================
now = time.time()
if now - st.session_state['last_monitor'] > 60 and st.session_state['test_positions']:
    st.session_state['last_monitor'] = now
    changed = False
    for p, data in list(st.session_state['test_positions'].items()):
        try:
            exch = exchange_futures if data.get('market_type') == 'FUTURES' else exchange_spot
            cur_p = exch.fetch_ticker(p)['last']
            if cur_p >= data['tp1']:
                archive_position(p, data, data['tp1'], "TP1 ✅")
                del st.session_state['test_positions'][p]
                changed = True
            elif cur_p <= data['sl']:
                archive_position(p, data, data['sl'], "SL ❌")
                del st.session_state['test_positions'][p]
                changed = True
        except:
            continue
    if changed:
        save_data(DB_FILE, st.session_state['test_positions'])
        st.rerun()

# ============================================================
# INTERFACE
# ============================================================
st.title("📟 SMC PRO V8 — SPOT & FUTURES | KuCoin")

# Test connexion
col_cx1, col_cx2 = st.columns(2)
try:
    btc_spot = exchange_spot.fetch_ticker('BTC/USDT')['last']
    col_cx1.success(f"✅ SPOT OK | BTC: {btc_spot:,.2f}$")
except Exception as e:
    col_cx1.error(f"❌ SPOT : {e}")
try:
    btc_fut = exchange_futures.fetch_ticker('XBTUSDTM')['last']
    col_cx2.success(f"✅ FUTURES OK | BTC: {btc_fut:,.2f}$")
except Exception as e:
    col_cx2.error(f"❌ FUTURES : {e}")

tab_scan, tab_journal = st.tabs(["🔎 SCANNER INTELLIGENT", "📈 JOURNAL & PERFORMANCE"])

with st.sidebar:
    st.header("⚙️ CONFIGURATION")
    mode_actuel = st.selectbox("STYLE DE TRADING", [
        "SCALPING (5m)",
        "RANGE MODE (Rebond)",
        "DAY TRADING (1h)",
        "SWING (4h)",
    ])
    st.divider()
    st.caption(f"Positions actives : {len(st.session_state['test_positions'])}")
    st.caption("Auto-monitoring : 60s")
    if st.button("🔄 ACTUALISER"):
        st.rerun()
    if st.button("🗑️ RESET JOURNAL"):
        save_data(DB_FILE, {})
        st.session_state['test_positions'] = {}
        st.rerun()

# ============================================================
# ONGLET 1 : SCANNER SPOT + FUTURES
# ============================================================
with tab_scan:
    col_list, col_focus = st.columns([1, 2])

    with col_list:
        st.subheader("🔎 Résultats")
        score_min = st.slider("Score minimum", 40, 90, 60)

        if st.button(f"🚀 LANCER SCAN — {mode_actuel}"):
            with st.status("Analyse SPOT + FUTURES en cours...", expanded=True) as status:

                all_results = []

                # ---- SCAN SPOT ----
                st.write("📦 Récupération paires SPOT...")
                try:
                    tickers_spot = exchange_spot.fetch_tickers()
                    pairs_spot = sorted(
                        [s for s in tickers_spot
                         if s.endswith('/USDT')
                         and 'UP/'   not in s
                         and 'DOWN/' not in s
                         and '3L/'   not in s
                         and '3S/'   not in s],
                        key=lambda x: tickers_spot[x].get('quoteVolume') or 0,
                        reverse=True
                    )[:40]
                    st.write(f"  → {len(pairs_spot)} paires SPOT à analyser")

                    for p in pairs_spot:
                        ana = get_market_analysis(exchange_spot, p, mode_actuel, "SPOT")
                        if ana and ana['score'] >= score_min:
                            if ("RANGE" in mode_actuel and ana['is_range']) or \
                               ("RANGE" not in mode_actuel and not ana['is_range']):
                                all_results.append({
                                    "sym":         p,
                                    "score":       ana['score'],
                                    "structure":   ana['structure_htf'],
                                    "market_type": "SPOT",
                                    "prix":        ana['prix'],
                                    "rsi":         ana['rsi'],
                                    "adx":         ana['adx'],
                                })
                except Exception as e:
                    st.warning(f"Erreur SPOT : {e}")

                # ---- SCAN FUTURES ----
                st.write("📊 Récupération paires FUTURES...")
                try:
                    tickers_fut = exchange_futures.fetch_tickers()
                    # KuCoin Futures : symboles XBTUSDTM, ETHUSDTM, etc.
                    pairs_fut = sorted(
                        [s for s in tickers_fut if s.endswith('USDTM')],
                        key=lambda x: tickers_fut[x].get('quoteVolume') or 0,
                        reverse=True
                    )[:40]
                    st.write(f"  → {len(pairs_fut)} paires FUTURES à analyser")

                    for p in pairs_fut:
                        ana = get_market_analysis(exchange_futures, p, mode_actuel, "FUTURES")
                        if ana and ana['score'] >= score_min:
                            if ("RANGE" in mode_actuel and ana['is_range']) or \
                               ("RANGE" not in mode_actuel and not ana['is_range']):
                                all_results.append({
                                    "sym":         p,
                                    "score":       ana['score'],
                                    "structure":   ana['structure_htf'],
                                    "market_type": "FUTURES",
                                    "prix":        ana['prix'],
                                    "rsi":         ana['rsi'],
                                    "adx":         ana['adx'],
                                })
                except Exception as e:
                    st.warning(f"Erreur FUTURES : {e}")

                all_results.sort(key=lambda x: x['score'], reverse=True)
                st.session_state['scan_res'] = all_results

                nb_spot = sum(1 for r in all_results if r['market_type'] == 'SPOT')
                nb_fut  = sum(1 for r in all_results if r['market_type'] == 'FUTURES')
                status.update(
                    label=f"✅ {len(all_results)} setup(s) — {nb_spot} SPOT · {nb_fut} FUTURES",
                    state="complete"
                )

        # --- LISTE RÉSULTATS ---
        if 'scan_res' in st.session_state:
            if st.session_state['scan_res']:
                st.caption(f"{len(st.session_state['scan_res'])} résultats — cliquer pour analyser")
                for r in st.session_state['scan_res']:
                    # Icône tendance
                    trend_icon = "🟢" if r['structure'] == "BULLISH" else "🔴" if r['structure'] == "BEARISH" else "🟡"
                    # Badge marché
                    mkt_badge  = "🔵 SPOT" if r['market_type'] == "SPOT" else "🟠 FUTURES"

                    label = (
                        f"{trend_icon} {mkt_badge}  |  {r['sym']}\n"
                        f"Score {r['score']}/100  ·  ADX {r['adx']:.0f}"
                        if r['adx'] else
                        f"{trend_icon} {mkt_badge}  |  {r['sym']}  |  Score {r['score']}/100"
                    )
                    if st.button(label, key=f"btn_{r['sym']}_{r['market_type']}"):
                        st.session_state['active_p']    = r['sym']
                        st.session_state['active_mkt']  = r['market_type']
            else:
                st.warning(
                    "Aucun résultat.\n\n"
                    "Essayez de baisser le score minimum (slider ci-dessus)."
                )

    # --- FOCUS PAIRE ---
    with col_focus:
        if 'active_p' in st.session_state and 'active_mkt' in st.session_state:
            p        = st.session_state['active_p']
            act_mkt  = st.session_state['active_mkt']
            exch_obj = exchange_futures if act_mkt == "FUTURES" else exchange_spot

            res = get_market_analysis(exch_obj, p, mode_actuel, act_mkt)

            if res:
                levels   = compute_levels(res, mode_actuel)
                mkt_tag  = "🟠 FUTURES" if act_mkt == "FUTURES" else "🔵 SPOT"

                st.header(f"💼 {p}  ·  {mkt_tag}  —  Score {res['score']}/100")

                col_htf, col_ltf = st.columns(2)
                col_htf.info(f"HTF ({res['htf']}) : **{res['structure_htf']}**")
                col_ltf.info(f"LTF ({res['ltf']}) : **{res['structure_ltf']}**")

                st.subheader("📊 Indicateurs")
                i1, i2, i3, i4 = st.columns(4)
                i1.metric("RSI", f"{res['rsi']:.1f}" if res['rsi'] else "N/A",
                          delta="Survendu"  if res['rsi'] and res['rsi'] < 35 else
                                "Suracheté" if res['rsi'] and res['rsi'] > 70 else "Neutre")
                i2.metric("ADX", f"{res['adx']:.1f}" if res['adx'] else "N/A",
                          delta="Range" if res['is_range'] else "Tendance")
                i3.metric("Vol Ratio", f"x{res['vol_ratio']:.2f}")
                i4.metric("BB Width",  f"{res['bb_width']*100:.2f}%" if res['bb_width'] else "N/A")

                st.subheader("🧩 Contexte SMC")
                smc1, smc2, smc3 = st.columns(3)
                smc1.write(f"**OB Bullish**: {res['ob_bull']:.6f}" if res['ob_bull'] else "**OB Bullish**: Aucun")
                smc2.write(f"**OB Bearish**: {res['ob_bear']:.6f}" if res['ob_bear'] else "**OB Bearish**: Aucun")
                if res['fvg']:
                    smc3.write(f"**FVG {res['fvg']['type']}**: {res['fvg']['low']:.4f} — {res['fvg']['high']:.4f}")
                else:
                    smc3.write("**FVG**: Aucun")

                st.subheader("📍 Niveaux de Trading")
                n1, n2, n3, n4 = st.columns(4)
                n1.error( f"SL\n{levels['risk_pct']:.2f}%\n{levels['sl']:.6f}")
                n2.warning(f"ENTRÉE\n—\n{levels['entry']:.6f}")
                n3.success(f"TP1 (+{levels['tp1_pct']:.2f}%)\nRR: {levels['rr_tp1']}\n{levels['tp1']:.6f}")
                n4.success(f"TP2 (+{levels['tp2_pct']:.2f}%)\nRR: {levels['rr_tp2']}\n{levels['tp2']:.6f}")
                st.caption(f"TP3 (runner, +{levels['tp3_pct']:.2f}%): {levels['tp3']:.6f}")

                if res['score'] >= 70:
                    st.success(f"✅ Setup QUALITÉ — Score {res['score']}/100 — R/R TP1: {levels['rr_tp1']}")
                elif res['score'] >= 55:
                    st.warning(f"⚠️ Setup MOYEN — Score {res['score']}/100 — Attendre confirmation")
                else:
                    st.error(f"❌ Setup FAIBLE — Score {res['score']}/100 — Éviter")

                if res['score'] >= 55:
                    if st.button("🚀 VALIDER ET SURVEILLER"):
                        st.session_state['test_positions'][f"{p}_{act_mkt}"] = {
                            "symbol":      p,
                            "entry":       levels['entry'],
                            "tp1":         levels['tp1'],
                            "tp2":         levels['tp2'],
                            "tp3":         levels['tp3'],
                            "sl":          levels['sl'],
                            "tp1_pct":     levels['tp1_pct'],
                            "risk_pct":    levels['risk_pct'],
                            "rr":          levels['rr_tp1'],
                            "style":       mode_actuel,
                            "score":       res['score'],
                            "structure":   res['structure_htf'],
                            "market_type": act_mkt,
                            "time_full":   datetime.now().strftime("%d/%m %H:%M:%S"),
                        }
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.success(f"Position {act_mkt} ouverte sur {p} ✅")
                        st.rerun()
                else:
                    st.info("Score insuffisant pour valider ce setup.")

# ============================================================
# ONGLET 2 : JOURNAL & PERFORMANCE
# ============================================================
with tab_journal:
    history = load_data(HIST_FILE)
    today   = datetime.now().strftime("%d/%m")

    daily_pnl, wins, losses = 0.0, 0, 0
    for h in history:
        if h.get('FERMETURE', '').startswith(today):
            pnl_str = h.get('PNL %', '0').replace('+', '').replace('%', '').strip()
            try:
                val = float(pnl_str)
                daily_pnl += val
                if val > 0: wins += 1
                else:       losses += 1
            except ValueError:
                continue

    total_trades = wins + losses
    win_rate     = (wins / total_trades * 100) if total_trades > 0 else 0
    avg_pnl_all  = 0.0
    if history:
        vals = []
        for h in history:
            try: vals.append(float(h.get('PNL %', '0').replace('+', '').replace('%', '').strip()))
            except: pass
        avg_pnl_all = sum(vals) / len(vals) if vals else 0.0

    st.subheader("📊 Performance du jour")
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("PROFIT DU JOUR",  f"{daily_pnl:+.2f}%",  delta=f"{daily_pnl - 5:.2f}% vs obj 5%")
    c2.metric("WIN RATE (jour)", f"{win_rate:.0f}%",     delta=f"{wins}W / {losses}L")
    c3.metric("TRADES (jour)",   str(total_trades))
    c4.metric("PNL MOY GLOBAL",  f"{avg_pnl_all:+.2f}%")

    st.progress(min(max(daily_pnl / 5, 0.0), 1.0),
                text=f"Progression : {daily_pnl:.2f}% / 5%")
    st.write("---")

    # Positions actives
    if st.session_state['test_positions']:
        st.subheader("🔴 POSITIONS ACTIVES")
        for key, data in list(st.session_state['test_positions'].items()):
            try:
                sym      = data.get('symbol', key.split('_')[0])
                mkt      = data.get('market_type', 'SPOT')
                exch_obj = exchange_futures if mkt == "FUTURES" else exchange_spot
                mkt_tag  = "🟠 FUTURES" if mkt == "FUTURES" else "🔵 SPOT"

                cur_p   = exch_obj.fetch_ticker(sym)['last']
                prog    = ((cur_p - data['entry']) / data['entry']) * 100
                dist_tp = ((data['tp1'] - cur_p) / cur_p) * 100

                # Auto TP/SL
                if cur_p >= data['tp1']:
                    archive_position(sym, data, data['tp1'], "TP1 ✅")
                    del st.session_state['test_positions'][key]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()
                elif cur_p <= data['sl']:
                    archive_position(sym, data, data['sl'], "SL ❌")
                    del st.session_state['test_positions'][key]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.rerun()

                color = "🟢" if prog > 0 else "🔴"
                with st.expander(
                    f"{color} {sym}  ·  {mkt_tag}  |  P&L: {prog:+.2f}%  |  Score: {data.get('score', 'N/A')}",
                    expanded=True
                ):
                    col_a, col_b, col_c, col_d = st.columns([2, 2, 2, 1])
                    col_a.metric("ENTRÉE",    f"{data['entry']:.6f}")
                    col_b.metric("PRIX LIVE", f"{cur_p:.6f}", f"{prog:+.2f}%")
                    col_c.metric("Dist TP1",  f"{dist_tp:+.2f}%")
                    if col_d.button("💰 VENDRE", key=f"sell_{key}"):
                        archive_position(sym, data, cur_p, "MANUEL 💼")
                        del st.session_state['test_positions'][key]
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()
                    st.caption(
                        f"SL: {data['sl']:.6f} ({data.get('risk_pct', 0):.2f}%)  |  "
                        f"TP1: {data['tp1']:.6f}  |  TP2: {data.get('tp2', 'N/A')}  |  "
                        f"Style: {data['style']}  |  Ouvert: {data.get('time_full', '?')}"
                    )
            except Exception as e:
                st.warning(f"Erreur lecture {key}: {e}")
                continue
    else:
        st.info("Aucune position active.")

    # Historique
    st.write("---")
    with st.expander("📜 HISTORIQUE DÉTAILLÉ", expanded=False):
        if history:
            df_hist    = pd.DataFrame(history)
            cols_ordre = ["SYMBOLE", "MARCHÉ", "OUVERTURE", "FERMETURE", "ENTREE",
                          "SORTIE", "PNL %", "RR", "SCORE", "RAISON", "STYLE"]
            cols_valides = [c for c in cols_ordre if c in df_hist.columns]
            st.dataframe(df_hist[cols_valides].iloc[::-1], use_container_width=True, height=400)
        else:
            st.info("L'historique est vide pour le moment.")
