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
        "MARCHÉ":    data.get('market_type', 'SPOT'),
        "OUVERTURE": data.get('time_full', "N/A"),
        "FERMETURE": datetime.now().strftime("%d/%m %H:%M:%S"),
        "PNL %":     f"{pnl:+.2f}%",
        "RAISON":    reason,
        "STYLE":     data['style'],
        "ENTREE":    f"{data['entry']:.6f}",
        "SORTIE":    f"{exit_price:.6f}",
        "RR":        data.get('rr', 'N/A'),
        "SCORE":     data.get('score', 'N/A'),
        "pnl":       round(pnl, 2),
        "date":      datetime.now().strftime("%Y-%m-%d"),
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
# CONFIG
# ============================================================
st.set_page_config(page_title="SMC V8 PRO — SPOT & FUTURES", layout="wide")

if 'test_positions'  not in st.session_state:
    st.session_state['test_positions']  = load_data(DB_FILE)
if 'last_monitor'    not in st.session_state:
    st.session_state['last_monitor']    = 0

exchange_spot    = get_spot_exchange()
exchange_futures = get_futures_exchange()

# ============================================================
# MOTEUR D'ANALYSE
# ============================================================
def fetch_df(exchange_obj, symbol, tf, limit=150):
    bars = exchange_obj.fetch_ohlcv(symbol, timeframe=tf, limit=limit)
    df   = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
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
    rh = [df['h'].iloc[i] for i in range(len(df)-20, len(df)) if df['h'].iloc[i] == highs.iloc[i]]
    rl = [df['l'].iloc[i] for i in range(len(df)-20, len(df)) if df['l'].iloc[i] == lows.iloc[i]]
    if len(rh) >= 2 and len(rl) >= 2:
        if rh[-1] > rh[-2] and rl[-1] > rl[-2]: return "BULLISH"
        if rh[-1] < rh[-2] and rl[-1] < rl[-2]: return "BEARISH"
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
        if gap > 0:   fvg = {'type':'bullish','low':df['h'].iloc[i-1],'high':df['l'].iloc[i+1]}
        elif gap < 0: fvg = {'type':'bearish','low':df['l'].iloc[i+1],'high':df['h'].iloc[i-1]}
    return ob_bull, ob_bear, fvg

def score_setup(r_htf, r_ltf, mode):
    score = 0
    last  = r_ltf

    # EMA alignment
    e20  = last.get('ema20')
    e50  = last.get('ema50')
    e200 = last.get('ema200')
    c    = last.get('c', 0)
    if e20 and e50 and e200:
        if c > e20 > e50 > e200:   score += 20
        elif c < e20 < e50 < e200: score += 20
        elif c > e50:              score += 10
        else:                      score += 5
    else:
        score += 5  # données insuffisantes = score partiel au lieu de 0

    # RSI
    rsi = last.get('rsi', 50)
    if rsi:
        if 40 < rsi < 65:          score += 15
        elif rsi < 35 or rsi > 70: score += 10  # opportunité reversal

    # MACD
    if last.get('macd') and last.get('macd_signal'):
        score += 15 if last['macd'] > last['macd_signal'] and last.get('macd_hist', 0) > 0 else 8

    # Volume
    vr = last.get('vol_ratio', 1)
    if vr and vr > 1.3:   score += 10
    elif vr and vr > 1.0: score += 5

    # Structure HTF
    struct = r_htf.get('structure', 'NEUTRAL')
    if struct == "BULLISH" and c > (e50 or 0):        score += 20
    elif struct == "BEARISH" and c < (e50 or 999999): score += 20
    else:                                              score += 8  # neutral = petit bonus

    # OB
    if r_ltf.get('ob_bull') or r_ltf.get('ob_bear'): score += 10

    # ADX — bonus seulement, pas de pénalité
    adx = last.get('adx', 20) or 20
    if "RANGE" in mode and adx < 25:      score += 10
    elif "RANGE" not in mode and adx > 20: score += 10
    elif "RANGE" not in mode and adx > 15: score += 5

    return min(score, 100)

def get_market_analysis(exchange_obj, symbol, mode_choisi, market_type="SPOT"):
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
        adx_val  = row_ltf.get('adx') or 20
        is_range = adx_val < 22

        return {
            "symbol":        symbol,
            "market_type":   market_type,
            "prix":          row_ltf['c'],
            "atr":           row_ltf.get('atr') or row_ltf['c'] * 0.01,
            "adx":           adx_val,
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
    except Exception as e:
        return None

def compute_levels(res, mode_choisi):
    prix = res['prix']
    atr  = res['atr'] or prix * 0.01

    if "RANGE" in mode_choisi and res.get('bb_lower'):
        entry = res['ob_bull'] if res['ob_bull'] else res['bb_lower']
    elif res['structure_htf'] == "BULLISH" and res.get('ema20'):
        entry = min(prix, res['ema20'])
    else:
        entry = prix

    if entry is None: entry = prix

    sl_atr = entry - atr * 1.5
    if res.get('ob_bull'):
        sl = min(sl_atr, res['ob_bull'] - atr * 0.3)
    elif res.get('bb_lower'):
        sl = min(sl_atr, res['bb_lower'] * 0.998)
    else:
        sl = sl_atr

    risk = entry - sl
    if risk <= 0: risk = atr * 1.5

    tp1 = entry + risk * 1.5
    tp2 = entry + risk * 2.5
    tp3 = entry + risk * 4.0

    if "RANGE" in mode_choisi and res.get('bb_upper'):
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
.main{background:#000;color:#00FF41;font-family:'Consolas',monospace}
.stMetric{background:#0a0a0a;border:1px solid #00FF41;padding:15px;border-radius:10px}
.stTabs [data-baseweb="tab"]{color:#00FF41;font-weight:bold;font-size:18px}
.stButton>button{border:1px solid #00FF41;background:#050505;color:#00FF41;width:100%}
.stButton>button:hover{background:#00FF41;color:#000}
</style>
""", unsafe_allow_html=True)

# ============================================================
# AUTO-MONITORING (60s)
# ============================================================
now = time.time()
if now - st.session_state['last_monitor'] > 60 and st.session_state['test_positions']:
    st.session_state['last_monitor'] = now
    changed = False
    for key, data in list(st.session_state['test_positions'].items()):
        try:
            sym      = data.get('symbol', key)
            exch_obj = exchange_futures if data.get('market_type') == 'FUTURES' else exchange_spot
            cur_p    = exch_obj.fetch_ticker(sym)['last']
            if cur_p >= data['tp1']:
                archive_position(sym, data, data['tp1'], "TP1 ✅")
                del st.session_state['test_positions'][key]; changed = True
            elif cur_p <= data['sl']:
                archive_position(sym, data, data['sl'], "SL ❌")
                del st.session_state['test_positions'][key]; changed = True
        except: continue
    if changed:
        save_data(DB_FILE, st.session_state['test_positions'])
        st.rerun()

# ============================================================
# HEADER
# ============================================================
st.title("📟 SMC PRO V8 — SPOT & FUTURES | KuCoin")

col_cx1, col_cx2 = st.columns(2)
try:
    bp = exchange_spot.fetch_ticker('BTC/USDT')['last']
    col_cx1.success(f"✅ SPOT OK | BTC: {bp:,.2f}$")
except Exception as e:
    col_cx1.error(f"❌ SPOT : {e}")
try:
    bf = exchange_futures.fetch_ticker('XBTUSDTM')['last']
    col_cx2.success(f"✅ FUTURES OK | BTC: {bf:,.2f}$")
except Exception as e:
    col_cx2.error(f"❌ FUTURES : {e}")

tab_scan, tab_journal = st.tabs(["🔎 SCANNER INTELLIGENT", "📈 JOURNAL & PERFORMANCE"])

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("⚙️ CONFIGURATION")
    mode_actuel = st.selectbox("STYLE DE TRADING", [
        "SCALPING (5m)",
        "RANGE MODE (Rebond)",
        "DAY TRADING (1h)",
        "SWING (4h)",
    ])
    st.divider()
    st.subheader("🔧 Filtres scanner")

    # ✅ FILTRE ADX SUPPRIMÉ — remplacé par score uniquement
    score_min   = st.slider("Score minimum",          20, 80, 40)
    nb_spot     = st.slider("Paires SPOT à scanner",  10, 80, 50)
    nb_futures  = st.slider("Paires FUTURES à scanner", 10, 60, 30)
    debug_mode  = st.checkbox("🔍 Mode debug (voir détails)", value=False)

    st.divider()
    st.caption(f"Positions actives : {len(st.session_state['test_positions'])}")
    st.caption("Auto-monitoring : 60s")
    if st.button("🔄 ACTUALISER"):    st.rerun()
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
        st.subheader("🔎 Résultats")

        if st.button(f"🚀 LANCER SCAN — {mode_actuel}"):
            all_results   = []
            debug_log     = []

            with st.status("Analyse SPOT + FUTURES...", expanded=True) as status:

                # ══════════════════════════════
                # SCAN SPOT
                # ══════════════════════════════
                st.write("📦 Récupération paires SPOT KuCoin...")
                try:
                    tickers_spot = exchange_spot.fetch_tickers()
                    pairs_spot   = sorted(
                        [s for s in tickers_spot
                         if s.endswith('/USDT')
                         and 'UP/'   not in s
                         and 'DOWN/' not in s
                         and '3L/'   not in s
                         and '3S/'   not in s],
                        key=lambda x: tickers_spot[x].get('quoteVolume') or 0,
                        reverse=True
                    )[:nb_spot]

                    st.write(f"  → {len(pairs_spot)} paires SPOT sélectionnées")
                    ok_s, fail_s = 0, 0

                    for p in pairs_spot:
                        ana = get_market_analysis(exchange_spot, p, mode_actuel, "SPOT")
                        if ana is None:
                            fail_s += 1
                            debug_log.append(f"[SPOT] {p} → ERREUR analyse")
                            continue

                        ok_s += 1
                        passes = ana['score'] >= score_min
                        debug_log.append(
                            f"[SPOT] {p} → score={ana['score']} adx={ana['adx']:.1f} "
                            f"rsi={ana['rsi']:.1f if ana['rsi'] else 'N/A'} → {'✅' if passes else '❌'}"
                        )
                        if passes:
                            all_results.append({
                                "sym":         p,
                                "score":       ana['score'],
                                "structure":   ana['structure_htf'],
                                "market_type": "SPOT",
                                "prix":        ana['prix'],
                                "rsi":         ana['rsi'],
                                "adx":         ana['adx'],
                                "is_range":    ana['is_range'],
                            })

                    st.write(f"  → SPOT : {ok_s} analysées, {len([r for r in all_results if r['market_type']=='SPOT'])} retenues, {fail_s} erreurs")

                except Exception as e:
                    st.warning(f"Erreur SPOT : {e}")

                # ══════════════════════════════
                # SCAN FUTURES
                # ══════════════════════════════
                st.write("📊 Récupération paires FUTURES KuCoin...")
                try:
                    markets_fut = exchange_futures.load_markets()

                    # KuCoin Futures — format réel des symboles perpetual USDT
                    pairs_fut = sorted(
                        [s for s in markets_fut
                         if markets_fut[s].get('quote') == 'USDT'
                         and markets_fut[s].get('type') in ('swap', 'future')
                         and markets_fut[s].get('active', True)],
                        key=lambda x: markets_fut[x].get('info', {}).get('volumeOf24h') or 0,
                        reverse=True
                    )[:nb_futures]

                    st.write(f"  → {len(pairs_fut)} paires FUTURES sélectionnées")
                    ok_f, fail_f = 0, 0

                    for p in pairs_fut:
                        ana = get_market_analysis(exchange_futures, p, mode_actuel, "FUTURES")
                        if ana is None:
                            fail_f += 1
                            debug_log.append(f"[FUT] {p} → ERREUR analyse")
                            continue

                        ok_f += 1
                        passes = ana['score'] >= score_min
                        debug_log.append(
                            f"[FUT] {p} → score={ana['score']} adx={ana['adx']:.1f} → {'✅' if passes else '❌'}"
                        )
                        if passes:
                            all_results.append({
                                "sym":         p,
                                "score":       ana['score'],
                                "structure":   ana['structure_htf'],
                                "market_type": "FUTURES",
                                "prix":        ana['prix'],
                                "rsi":         ana['rsi'],
                                "adx":         ana['adx'],
                                "is_range":    ana['is_range'],
                            })

                    st.write(f"  → FUTURES : {ok_f} analysées, {len([r for r in all_results if r['market_type']=='FUTURES'])} retenues, {fail_f} erreurs")

                except Exception as e:
                    st.warning(f"Erreur FUTURES : {e}")

                # ══════════════════════════════
                all_results.sort(key=lambda x: x['score'], reverse=True)
                st.session_state['scan_res']   = all_results
                st.session_state['debug_log']  = debug_log

                nb_s = sum(1 for r in all_results if r['market_type'] == 'SPOT')
                nb_f = sum(1 for r in all_results if r['market_type'] == 'FUTURES')
                status.update(
                    label=f"✅ {len(all_results)} setup(s) — {nb_s} SPOT · {nb_f} FUTURES",
                    state="complete"
                )

        # ── Affichage résultats ──
        if 'scan_res' in st.session_state:
            results = st.session_state['scan_res']

            if results:
                st.caption(f"{len(results)} setup(s) — classés par score ↓")
                for r in results:
                    trend_icon = "🟢" if r['structure'] == "BULLISH" else "🔴" if r['structure'] == "BEARISH" else "🟡"
                    mkt_badge  = "🔵 SPOT" if r['market_type'] == "SPOT" else "🟠 FUTURES"
                    rng_tag    = " · Range" if r.get('is_range') else " · Trend"
                    label = f"{trend_icon} {mkt_badge}  |  {r['sym']}  |  {r['score']}/100{rng_tag}"
                    if st.button(label, key=f"btn_{r['sym']}_{r['market_type']}"):
                        st.session_state['active_p']   = r['sym']
                        st.session_state['active_mkt'] = r['market_type']
            else:
                st.error("❌ Aucun setup trouvé.")
                st.info(
                    "**Causes possibles :**\n"
                    "- Score minimum trop élevé → baissez à **20** dans la sidebar\n"
                    "- Marché très calme (week-end / nuit)\n"
                    "- Erreurs API KuCoin (rate limit)\n\n"
                    "Activez **Mode debug** dans la sidebar pour voir le détail."
                )

            # Debug log
            if debug_mode and 'debug_log' in st.session_state:
                with st.expander("🔍 Debug — détail de chaque paire", expanded=False):
                    for line in st.session_state['debug_log']:
                        color = "green" if "✅" in line else "red" if "ERREUR" in line else "gray"
                        st.markdown(f"<span style='color:{color};font-size:11px'>{line}</span>", unsafe_allow_html=True)

    # ── FOCUS PAIRE ──
    with col_focus:
        if 'active_p' in st.session_state and 'active_mkt' in st.session_state:
            p        = st.session_state['active_p']
            act_mkt  = st.session_state['active_mkt']
            exch_obj = exchange_futures if act_mkt == "FUTURES" else exchange_spot
            mkt_tag  = "🟠 FUTURES" if act_mkt == "FUTURES" else "🔵 SPOT"

            res = get_market_analysis(exch_obj, p, mode_actuel, act_mkt)

            if res:
                levels = compute_levels(res, mode_actuel)
                st.header(f"💼 {p}  ·  {mkt_tag}  —  Score {res['score']}/100")

                col_htf, col_ltf = st.columns(2)
                col_htf.info(f"HTF ({res['htf']}) : **{res['structure_htf']}**")
                col_ltf.info(f"LTF ({res['ltf']}) : **{res['structure_ltf']}**")

                st.subheader("📊 Indicateurs")
                i1, i2, i3, i4 = st.columns(4)
                i1.metric("RSI",      f"{res['rsi']:.1f}" if res['rsi'] else "N/A",
                          "Survendu"  if res['rsi'] and res['rsi'] < 35 else
                          "Suracheté" if res['rsi'] and res['rsi'] > 70 else "Neutre")
                i2.metric("ADX",      f"{res['adx']:.1f}", "Range" if res['is_range'] else "Tendance")
                i3.metric("Vol Ratio",f"x{res['vol_ratio']:.2f}")
                i4.metric("BB Width", f"{res['bb_width']*100:.2f}%" if res['bb_width'] else "N/A")

                st.subheader("🧩 Contexte SMC")
                s1, s2, s3 = st.columns(3)
                s1.write(f"**OB Bull**: {res['ob_bull']:.6f}" if res['ob_bull'] else "**OB Bull**: —")
                s2.write(f"**OB Bear**: {res['ob_bear']:.6f}" if res['ob_bear'] else "**OB Bear**: —")
                if res['fvg']:
                    s3.write(f"**FVG {res['fvg']['type']}**: {res['fvg']['low']:.4f}–{res['fvg']['high']:.4f}")
                else:
                    s3.write("**FVG**: —")

                st.subheader("📍 Niveaux")
                n1, n2, n3, n4 = st.columns(4)
                n1.error( f"SL\n{levels['risk_pct']:.2f}%\n{levels['sl']:.6f}")
                n2.warning(f"ENTRÉE\n—\n{levels['entry']:.6f}")
                n3.success(f"TP1 +{levels['tp1_pct']:.2f}%\nRR {levels['rr_tp1']}\n{levels['tp1']:.6f}")
                n4.success(f"TP2 +{levels['tp2_pct']:.2f}%\nRR {levels['rr_tp2']}\n{levels['tp2']:.6f}")
                st.caption(f"TP3 runner +{levels['tp3_pct']:.2f}% → {levels['tp3']:.6f}")

                if res['score'] >= 70:   st.success(f"✅ Setup QUALITÉ — Score {res['score']}/100")
                elif res['score'] >= 50: st.warning(f"⚠️ Setup MOYEN — Score {res['score']}/100")
                else:                    st.error(  f"❌ Setup FAIBLE — Score {res['score']}/100")

                if st.button("🚀 VALIDER ET SURVEILLER"):
                    key = f"{p}_{act_mkt}"
                    st.session_state['test_positions'][key] = {
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
                    st.success(f"✅ {p} [{act_mkt}] ajouté au journal")
                    st.rerun()

# ============================================================
# ONGLET 2 : JOURNAL
# ============================================================
with tab_journal:
    history = load_data(HIST_FILE)
    today   = datetime.now().strftime("%d/%m")

    daily_pnl, wins, losses = 0.0, 0, 0
    for h in history:
        if h.get('FERMETURE', '').startswith(today):
            try:
                val = float(h.get('PNL %','0').replace('+','').replace('%','').strip())
                daily_pnl += val
                if val > 0: wins += 1
                else:       losses += 1
            except: continue

    total_trades = wins + losses
    win_rate     = (wins / total_trades * 100) if total_trades > 0 else 0
    vals_all     = []
    for h in history:
        try: vals_all.append(float(h.get('PNL %','0').replace('+','').replace('%','').strip()))
        except: pass
    avg_pnl_all = sum(vals_all) / len(vals_all) if vals_all else 0.0

    st.subheader("📊 Performance du jour")
    j1, j2, j3, j4 = st.columns(4)
    j1.metric("PROFIT DU JOUR",  f"{daily_pnl:+.2f}%", f"{daily_pnl-5:.2f}% vs obj 5%")
    j2.metric("WIN RATE (jour)", f"{win_rate:.0f}%",    f"{wins}W / {losses}L")
    j3.metric("TRADES (jour)",   str(total_trades))
    j4.metric("PNL MOY GLOBAL",  f"{avg_pnl_all:+.2f}%", f"{len(history)} trades total")
    st.progress(min(max(daily_pnl/5, 0.0), 1.0), text=f"Progression : {daily_pnl:.2f}% / 5%")
    st.write("---")

    if st.session_state['test_positions']:
        st.subheader("🔴 POSITIONS ACTIVES")
        for key, data in list(st.session_state['test_positions'].items()):
            try:
                sym      = data.get('symbol', key)
                mkt      = data.get('market_type', 'SPOT')
                exch_obj = exchange_futures if mkt == "FUTURES" else exchange_spot
                mkt_tag  = "🟠 FUTURES" if mkt == "FUTURES" else "🔵 SPOT"
                cur_p    = exch_obj.fetch_ticker(sym)['last']
                prog     = ((cur_p - data['entry']) / data['entry']) * 100
                dist_tp  = ((data['tp1'] - cur_p) / cur_p) * 100

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

                icon = "🟢" if prog > 0 else "🔴"
                with st.expander(f"{icon} {sym} · {mkt_tag} | P&L {prog:+.2f}% | Score: {data.get('score','N/A')}", expanded=True):
                    ca, cb, cc, cd = st.columns([2,2,2,1])
                    ca.metric("ENTRÉE",    f"{data['entry']:.6f}")
                    cb.metric("PRIX LIVE", f"{cur_p:.6f}", f"{prog:+.2f}%")
                    cc.metric("Dist TP1",  f"{dist_tp:+.2f}%")
                    if cd.button("💰 VENDRE", key=f"sell_{key}"):
                        archive_position(sym, data, cur_p, "MANUEL 💼")
                        del st.session_state['test_positions'][key]
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()
                    st.caption(f"SL: {data['sl']:.6f} ({data.get('risk_pct',0):.2f}%) | TP1: {data['tp1']:.6f} | TP2: {data.get('tp2','N/A')} | Style: {data['style']}")
            except Exception as e:
                st.warning(f"Erreur {key}: {e}")
    else:
        st.info("Aucune position active.")

    st.write("---")
    with st.expander("📜 HISTORIQUE DÉTAILLÉ", expanded=False):
        if history:
            df_hist = pd.DataFrame(history)
            cols    = ["SYMBOLE","MARCHÉ","OUVERTURE","FERMETURE","ENTREE","SORTIE","PNL %","RR","SCORE","RAISON","STYLE"]
            ok_cols = [c for c in cols if c in df_hist.columns]
            st.dataframe(df_hist[ok_cols].iloc[::-1], use_container_width=True, height=400)
        else:
            st.info("Historique vide.")
