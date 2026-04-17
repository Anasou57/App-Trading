import streamlit as st
import ccxt
import pandas as pd
import pandas_ta as ta
import json
import os
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
        "pnl":       round(pnl, 2),
        "date":      datetime.now().strftime("%Y-%m-%d"),
    })
    save_data(HIST_FILE, history)
    return pnl

# ============================================================
# EXCHANGES (SPOT + FUTURES)
# ============================================================
@st.cache_resource
def get_exchange(market_type: str):
    if market_type == "FUTURES":
        return ccxt.kucoinfutures({'timeout': 30000, 'enableRateLimit': True})
    return ccxt.kucoin({'timeout': 30000, 'enableRateLimit': True})

# ============================================================
# CONFIG STREAMLIT
# ============================================================
st.set_page_config(page_title="SMC V8 — SPOT/FUTURES", layout="wide")

if 'test_positions' not in st.session_state:
    st.session_state['test_positions'] = load_data(DB_FILE)

# ============================================================
# SIDEBAR
# ============================================================
with st.sidebar:
    st.header("⚙️ CONFIGURATION")

    market_type = st.radio("MARCHÉ", ["SPOT", "FUTURES"], horizontal=True)

    mode_actuel = st.selectbox("STYLE DE TRADING", [
        "SCALPING (5m)",
        "RANGE MODE (Rebond)",
        "DAY TRADING (1h)",
        "SWING (4h)",
    ])

    st.divider()

    # Paramètres du scanner
    st.subheader("🔧 Paramètres scan")
    nb_pairs     = st.slider("Nombre de paires à scanner", 20, 100, 50)
    adx_min      = st.slider("ADX minimum (tendance)",  10, 40, 15)
    adx_max      = st.slider("ADX maximum (range)",     15, 45, 35)
    vol_filter   = st.slider("Volume 24h minimum (M$)", 0, 50, 2)

    st.divider()
    st.caption(f"Positions actives : {len(st.session_state['test_positions'])}")
    if st.button("🔄 ACTUALISER"):
        st.rerun()
    if st.button("🗑️ RESET JOURNAL"):
        save_data(DB_FILE, {})
        st.session_state['test_positions'] = {}
        st.rerun()

exchange = get_exchange(market_type)

# ============================================================
# MOTEUR D'ANALYSE — MULTI-TF + SCORE
# ============================================================
TF_MAP = {
    "SCALPING (5m)":      ("15m", "5m"),
    "RANGE MODE (Rebond)":("1h",  "15m"),
    "DAY TRADING (1h)":   ("4h",  "1h"),
    "SWING (4h)":         ("1d",  "4h"),
}

def fetch_df(sym, tf, limit=150):
    bars = exchange.fetch_ohlcv(sym, timeframe=tf, limit=limit)
    df   = pd.DataFrame(bars, columns=['t','o','h','l','c','v'])
    return df

def add_indicators(df):
    df['ema20']  = ta.ema(df['c'], length=20)
    df['ema50']  = ta.ema(df['c'], length=50)
    df['ema200'] = ta.ema(df['c'], length=200)
    df['rsi']    = ta.rsi(df['c'], length=14)
    df['atr']    = ta.atr(df['h'], df['l'], df['c'], length=14)

    macd = ta.macd(df['c'], fast=12, slow=26, signal=9)
    if macd is not None:
        df['macd_hist'] = macd.iloc[:, 2]   # colonne histogramme

    bb = ta.bbands(df['c'], length=20, std=2)
    if bb is not None:
        df['bb_upper'] = bb.iloc[:, 0]
        df['bb_lower'] = bb.iloc[:, 2]
        df['bb_mid']   = bb.iloc[:, 1]

    adx_df = ta.adx(df['h'], df['l'], df['c'], length=14)
    if adx_df is not None:
        df['adx'] = adx_df.iloc[:, 0]
        df['dmp'] = adx_df.iloc[:, 1]
        df['dmn'] = adx_df.iloc[:, 2]

    df['vol_ratio'] = df['v'] / df['v'].rolling(20).mean()
    return df

def score_pair(ltf_row, htf_row, mode):
    """Score 0–100 basé sur alignement HTF + signaux LTF."""
    s = 0

    # --- Structure EMA LTF ---
    c   = ltf_row.get('c',   0)
    e20 = ltf_row.get('ema20', None)
    e50 = ltf_row.get('ema50', None)
    e200= ltf_row.get('ema200', None)
    if e20 and e50 and e200:
        if c > e20 > e50 > e200:   s += 20
        elif c < e20 < e50 < e200: s += 20
        elif c > e50:              s += 10
        else:                      s += 5

    # --- RSI ---
    rsi = ltf_row.get('rsi', 50)
    if rsi:
        if 35 < rsi < 65:          s += 15
        elif rsi < 30 or rsi > 72: s += 10   # oversold/overbought = opportunité

    # --- MACD ---
    mh = ltf_row.get('macd_hist', None)
    if mh is not None:
        s += 10 if mh > 0 else 5

    # --- Volume ---
    vr = ltf_row.get('vol_ratio', 1)
    if vr and vr > 1.5:  s += 10
    elif vr and vr > 1.1: s += 5

    # --- ADX selon mode ---
    adx = ltf_row.get('adx', 20)
    if adx:
        if "RANGE" in mode and adx < 25:           s += 20
        elif "RANGE" not in mode and adx > 20:     s += 15
        elif "RANGE" not in mode and adx > 15:     s += 8

    # --- Alignement HTF (tendance directrice) ---
    hc   = htf_row.get('c',    0)
    he50 = htf_row.get('ema50', None)
    he20 = htf_row.get('ema20', None)
    if he50 and hc > he50:  s += 10
    if he20 and he50 and he20 > he50: s += 5

    return min(s, 100)

def get_analysis(symbol, mode):
    """Analyse complète : HTF + LTF + score + niveaux."""
    try:
        htf_tf, ltf_tf = TF_MAP.get(mode, ("1h", "15m"))

        df_htf = add_indicators(fetch_df(symbol, htf_tf))
        df_ltf = add_indicators(fetch_df(symbol, ltf_tf))

        htf = df_htf.iloc[-1].to_dict()
        ltf = df_ltf.iloc[-1].to_dict()

        score   = score_pair(ltf, htf, mode)
        adx_val = ltf.get('adx', 20) or 20
        is_range= adx_val < 25

        return {
            "symbol":    symbol,
            "prix":      ltf['c'],
            "atr":       ltf.get('atr', ltf['c'] * 0.01),
            "adx":       adx_val,
            "rsi":       ltf.get('rsi'),
            "macd_hist": ltf.get('macd_hist'),
            "ema20":     ltf.get('ema20'),
            "ema50":     ltf.get('ema50'),
            "ema200":    ltf.get('ema200'),
            "bb_upper":  ltf.get('bb_upper'),
            "bb_lower":  ltf.get('bb_lower'),
            "vol_ratio": ltf.get('vol_ratio', 1),
            "is_range":  is_range,
            "score":     score,
            "htf_tf":    htf_tf,
            "ltf_tf":    ltf_tf,
            "htf_ema50": htf.get('ema50'),
            "htf_trend": "BULL" if htf.get('ema20') and htf.get('ema50') and htf['ema20'] > htf['ema50'] else "BEAR",
        }
    except:
        return None

def compute_levels(res, mode):
    """Calcule entrée, SL, TP1/2/3 adaptés au mode."""
    prix  = res['prix']
    atr   = res['atr'] or prix * 0.01

    # Entrée
    if "RANGE" in mode and res['bb_lower']:
        entry = res['bb_lower']
    elif res['ema20']:
        entry = min(prix, res['ema20'])
    else:
        entry = prix

    # SL : sous bb_lower ou ATR×1.5
    sl_atr = entry - atr * 1.5
    if res['bb_lower']:
        sl = min(sl_atr, res['bb_lower'] * 0.997)
    else:
        sl = sl_atr

    risk = entry - sl
    if risk <= 0: risk = atr * 1.5

    # TP scaling
    if "RANGE" in mode and res['bb_upper']:
        tp1 = res['bb_upper'] * 0.997
        tp2 = res['bb_upper'] * 1.005
    else:
        tp1 = entry + risk * 1.5
        tp2 = entry + risk * 2.5

    tp3 = entry + risk * 4.0

    def pct(a, b): return ((b - a) / a) * 100

    return {
        "entry":    entry,
        "sl":       sl,
        "tp1":      tp1,
        "tp2":      tp2,
        "tp3":      tp3,
        "risk_pct": pct(entry, sl),
        "tp1_pct":  pct(entry, tp1),
        "tp2_pct":  pct(entry, tp2),
        "tp3_pct":  pct(entry, tp3),
        "rr1":      round(abs(tp1 - entry) / abs(risk), 2),
        "rr2":      round(abs(tp2 - entry) / abs(risk), 2),
    }

# ============================================================
# UTILITAIRES FORMAT
# ============================================================
def fmt(v):
    if v is None or (isinstance(v, float) and v != v): return "N/A"
    if v > 1000:  return f"{v:,.2f}"
    if v > 1:     return f"{v:.4f}"
    return f"{v:.8f}"

# ============================================================
# STYLE
# ============================================================
st.markdown("""
<style>
.main{background:#000;color:#00FF41;font-family:'Consolas',monospace}
.stMetric{background:#0a0a0a;border:1px solid #00FF41;padding:15px;border-radius:10px}
.stTabs [data-baseweb="tab"]{color:#00FF41;font-weight:bold;font-size:16px}
.stButton>button{border:1px solid #00FF41;background:#050505;color:#00FF41;width:100%}
.stButton>button:hover{background:#00FF41;color:#000}
</style>
""", unsafe_allow_html=True)

# ============================================================
# HEADER
# ============================================================
st.title(f"📟 SMC V8 — {market_type}")

try:
    btc_sym = "XBTUSDTM" if market_type == "FUTURES" else "BTC/USDT"
    btc_p   = exchange.fetch_ticker(btc_sym)['last']
    st.success(f"✅ KuCoin {market_type} OK | BTC: {btc_p:,.2f}$")
except Exception as e:
    st.error(f"❌ Connexion : {e}")

# ============================================================
# TABS
# ============================================================
tab_scan, tab_search, tab_journal = st.tabs([
    "🔎 SCANNER", "🔍 RECHERCHE PAIRE", "📈 JOURNAL & PERFORMANCE"
])

# ============================================================
# TAB 1 — SCANNER
# ============================================================
with tab_scan:
    col_list, col_focus = st.columns([1, 2])

    with col_list:
        st.subheader("Résultats")

        if st.button(f"🚀 LANCER SCAN — {mode_actuel}"):
            with st.status("Analyse en cours...", expanded=True) as status:
                try:
                    tickers = exchange.fetch_tickers()
                except:
                    tickers = {}

                # --- FILTRAGE PAIRES ---
                if market_type == "FUTURES":
                    # KuCoin Futures : symboles type XBTUSDTM, ETHUSDTM...
                    raw = [s for s in tickers
                           if s.endswith('USDTM') or s.endswith('/USDT:USDT')]
                else:
                    raw = [s for s in tickers if s.endswith('/USDT')
                           and 'UP/' not in s and 'DOWN/' not in s and '3L' not in s and '3S' not in s]

                # Tri par volume et filtre volume minimum
                def get_vol(sym):
                    t = tickers.get(sym, {})
                    return t.get('quoteVolume') or 0

                raw_sorted = sorted(raw, key=get_vol, reverse=True)

                # Appliquer le filtre volume (en millions)
                raw_filtered = [s for s in raw_sorted if get_vol(s) >= vol_filter * 1_000_000]

                pairs_to_scan = raw_filtered[:nb_pairs]
                st.write(f"📋 {len(pairs_to_scan)} paires à analyser (vol ≥ {vol_filter}M$)")

                results = []
                for i, p in enumerate(pairs_to_scan):
                    if i % 5 == 0:
                        st.write(f"Scan {i+1}/{len(pairs_to_scan)}...")
                    ana = get_analysis(p, mode_actuel)
                    if ana is None:
                        continue

                    # Filtre ADX adaptatif (utilise les sliders sidebar)
                    adx = ana['adx']
                    if "RANGE" in mode_actuel:
                        passes = adx <= adx_max
                    else:
                        passes = adx >= adx_min

                    if passes:
                        results.append({
                            "sym":     p,
                            "score":   ana['score'],
                            "adx":     adx,
                            "rsi":     ana.get('rsi'),
                            "trend":   ana.get('htf_trend', '?'),
                            "vol":     get_vol(p) / 1_000_000,
                        })

                results.sort(key=lambda x: x['score'], reverse=True)
                st.session_state['scan_res'] = results
                status.update(
                    label=f"✅ {len(results)} setup(s) trouvé(s) sur {len(pairs_to_scan)} paires",
                    state="complete"
                )

        # Affichage liste résultats
        if 'scan_res' in st.session_state and st.session_state['scan_res']:
            st.caption(f"{len(st.session_state['scan_res'])} résultats — cliquer pour analyser")
            for r in st.session_state['scan_res']:
                icon  = "🟢" if r['trend'] == "BULL" else "🔴"
                label = (f"{icon} {r['sym']}  |  Score {r['score']}/100  "
                         f"|  ADX {r['adx']:.0f}  |  Vol {r['vol']:.1f}M$")
                if st.button(label, key=f"btn_{r['sym']}"):
                    st.session_state['active_p'] = r['sym']
        elif 'scan_res' in st.session_state:
            st.warning(
                "Aucun résultat. Essayez :\n"
                "• Diminuer le filtre volume (sidebar)\n"
                "• Baisser l'ADX minimum\n"
                "• Augmenter ADX maximum\n"
                "• Augmenter le nombre de paires à scanner"
            )

    # --- FOCUS PAIRE ---
    with col_focus:
        if 'active_p' in st.session_state:
            p   = st.session_state['active_p']
            res = get_analysis(p, mode_actuel)

            if res:
                levels = compute_levels(res, mode_actuel)
                st.header(f"💼 {p}  —  Score {res['score']}/100")
                st.caption(f"HTF ({res['htf_tf']}) Tendance: **{res['htf_trend']}**  |  LTF ({res['ltf_tf']})")

                # Indicateurs
                ic1, ic2, ic3, ic4 = st.columns(4)
                ic1.metric("PRIX",    fmt(res['prix']))
                ic2.metric("RSI",     f"{res['rsi']:.1f}" if res['rsi'] else "N/A",
                           "Survendu" if res['rsi'] and res['rsi'] < 35 else
                           "Suracheté" if res['rsi'] and res['rsi'] > 70 else "OK")
                ic3.metric("ADX",     f"{res['adx']:.1f}",
                           "Range" if res['is_range'] else "Tendance forte")
                ic4.metric("Vol ×",   f"{res['vol_ratio']:.2f}" if res['vol_ratio'] else "N/A")

                # EMA context
                st.caption(
                    f"EMA20: {fmt(res['ema20'])}  |  EMA50: {fmt(res['ema50'])}  |  "
                    f"EMA200: {fmt(res['ema200'])}  |  MACD hist: "
                    f"{'+' if res['macd_hist'] and res['macd_hist'] > 0 else ''}"
                    f"{res['macd_hist']:.6f}" if res['macd_hist'] else "EMA200: N/A"
                )

                # Niveaux
                st.subheader("📍 Niveaux")
                l1, l2, l3, l4 = st.columns(4)
                l1.error( f"SL\n{levels['risk_pct']:.2f}%\n{fmt(levels['sl'])}")
                l2.warning(f"ENTRÉE\n—\n{fmt(levels['entry'])}")
                l3.success(f"TP1 +{levels['tp1_pct']:.2f}%\nRR {levels['rr1']}\n{fmt(levels['tp1'])}")
                l4.success(f"TP2 +{levels['tp2_pct']:.2f}%\nRR {levels['rr2']}\n{fmt(levels['tp2'])}")
                st.caption(f"TP3 (runner) +{levels['tp3_pct']:.2f}% → {fmt(levels['tp3'])}")

                # Verdict
                if res['score'] >= 70:
                    st.success(f"✅ Setup FORT — Score {res['score']}/100")
                elif res['score'] >= 50:
                    st.warning(f"⚠️ Setup MOYEN — Score {res['score']}/100 — Attendre confirmation")
                else:
                    st.error(f"❌ Setup FAIBLE — Score {res['score']}/100")

                # Bouton valider (disponible dès score >= 40)
                if res['score'] >= 40:
                    if st.button("🚀 VALIDER ET SURVEILLER", key="validate_scan"):
                        st.session_state['test_positions'][p] = {
                            "entry":       levels['entry'],
                            "tp":          levels['tp1'],   # compatibilité journal
                            "tp1":         levels['tp1'],
                            "tp2":         levels['tp2'],
                            "tp3":         levels['tp3'],
                            "sl":          levels['sl'],
                            "tp1_pct":     levels['tp1_pct'],
                            "risk_pct":    levels['risk_pct'],
                            "rr":          levels['rr1'],
                            "style":       mode_actuel,
                            "score":       res['score'],
                            "market_type": market_type,
                            "time_full":   datetime.now().strftime("%d/%m %H:%M:%S"),
                        }
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.success(f"✅ {p} ajouté au journal")
                        st.rerun()
                else:
                    st.info("Score trop faible pour valider (minimum 40).")

# ============================================================
# TAB 2 — RECHERCHE MANUELLE
# ============================================================
with tab_search:
    st.subheader(f"🔍 Analyser une paire — {market_type}")

    # Hint selon le marché
    hint = "XBTUSDTM, ETHUSDTM, SOLUSDTM..." if market_type == "FUTURES" else "BTC/USDT, ETH, SOL, BNB..."
    pair_raw = st.text_input(f"Symbole ({hint})", key="manual_search").upper().strip()

    if pair_raw:
        # Normalisation du symbole
        if market_type == "FUTURES":
            sym = pair_raw if 'USDTM' in pair_raw or '/' in pair_raw else f"{pair_raw}USDTM"
        else:
            sym = pair_raw if '/' in pair_raw else f"{pair_raw}/USDT"

        res = get_analysis(sym, mode_actuel)

        if res:
            levels = compute_levels(res, mode_actuel)
            st.header(f"💼 {sym}  —  Score {res['score']}/100")
            st.caption(f"HTF ({res['htf_tf']}) : **{res['htf_trend']}** | LTF ({res['ltf_tf']})")

            mc1, mc2, mc3, mc4 = st.columns(4)
            mc1.metric("PRIX",  fmt(res['prix']))
            mc2.metric("RSI",   f"{res['rsi']:.1f}" if res['rsi'] else "N/A")
            mc3.metric("ADX",   f"{res['adx']:.1f}", "Range" if res['is_range'] else "Trend")
            mc4.metric("ATR",   fmt(res['atr']))

            st.write("---")
            sc1, sc2, sc3, sc4 = st.columns(4)
            sc1.error( f"SL\n{levels['risk_pct']:.2f}%\n{fmt(levels['sl'])}")
            sc2.warning(f"ENTRÉE\n—\n{fmt(levels['entry'])}")
            sc3.success(f"TP1 +{levels['tp1_pct']:.2f}%\nRR {levels['rr1']}\n{fmt(levels['tp1'])}")
            sc4.success(f"TP2 +{levels['tp2_pct']:.2f}%\nRR {levels['rr2']}\n{fmt(levels['tp2'])}")
            st.caption(f"TP3 +{levels['tp3_pct']:.2f}% → {fmt(levels['tp3'])}")

            if st.button(f"🚀 SURVEILLER {sym}", key="btn_add_manual"):
                st.session_state['test_positions'][sym] = {
                    "entry":       levels['entry'],
                    "tp":          levels['tp1'],
                    "tp1":         levels['tp1'],
                    "tp2":         levels['tp2'],
                    "tp3":         levels['tp3'],
                    "sl":          levels['sl'],
                    "tp1_pct":     levels['tp1_pct'],
                    "risk_pct":    levels['risk_pct'],
                    "rr":          levels['rr1'],
                    "style":       mode_actuel,
                    "score":       res['score'],
                    "market_type": market_type,
                    "time_full":   datetime.now().strftime("%d/%m %H:%M:%S"),
                }
                save_data(DB_FILE, st.session_state['test_positions'])
                st.success(f"✅ {sym} ajouté au journal")
                st.rerun()
        else:
            st.error(f"Paire '{sym}' introuvable sur KuCoin {market_type}.")
            if market_type == "FUTURES":
                st.info("Format Futures KuCoin : XBTUSDTM, ETHUSDTM, SOLUSDTM, BNBUSDTM...")
            else:
                st.info("Format Spot : BTC/USDT, ETH/USDT...")

# ============================================================
# TAB 3 — JOURNAL & PERFORMANCE
# ============================================================
with tab_journal:
    history = load_data(HIST_FILE)
    today   = datetime.now().strftime("%d/%m")

    # Stats du jour
    daily_pnl, wins, losses = 0.0, 0, 0
    for h in history:
        if h.get('FERMETURE', '').startswith(today):
            try:
                val = float(h.get('PNL %','0').replace('+','').replace('%','').strip())
                daily_pnl += val
                if val > 0: wins += 1
                else:       losses += 1
            except: continue

    total_today = wins + losses
    win_rate    = (wins / total_today * 100) if total_today > 0 else 0

    avg_all = 0.0
    if history:
        vals = []
        for h in history:
            try: vals.append(float(h.get('PNL %','0').replace('+','').replace('%','').strip()))
            except: continue
        avg_all = sum(vals) / len(vals) if vals else 0.0

    st.subheader("📊 Performance du jour")
    j1, j2, j3, j4 = st.columns(4)
    j1.metric("PROFIT DU JOUR",  f"{daily_pnl:+.2f}%", f"{daily_pnl-5:.2f}% vs obj 5%")
    j2.metric("WIN RATE (jour)", f"{win_rate:.0f}%",    f"{wins}W / {losses}L")
    j3.metric("TRADES (jour)",   str(total_today))
    j4.metric("PNL MOY GLOBAL",  f"{avg_all:+.2f}%",   f"{len(history)} trades total")

    st.progress(min(max(daily_pnl / 5, 0.0), 1.0),
                text=f"Progression : {daily_pnl:.2f}% / 5%")
    st.write("---")

    # Positions actives
    if st.session_state['test_positions']:
        st.subheader("🔴 POSITIONS ACTIVES")
        for p, data in list(st.session_state['test_positions'].items()):
            try:
                cur_p = exchange.fetch_ticker(p)['last']
                prog  = ((cur_p - data['entry']) / data['entry']) * 100

                # Vérif TP/SL automatique
                if cur_p >= data.get('tp1', data.get('tp', 9e15)):
                    pnl = archive_position(p, data, data.get('tp1', data['tp']), "TP1 ✅")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.success(f"TP1 atteint sur {p} — PnL: {pnl:+.2f}%")
                    st.rerun()
                elif cur_p <= data['sl']:
                    pnl = archive_position(p, data, data['sl'], "SL ❌")
                    del st.session_state['test_positions'][p]
                    save_data(DB_FILE, st.session_state['test_positions'])
                    st.error(f"SL touché sur {p} — PnL: {pnl:+.2f}%")
                    st.rerun()

                icon = "🟢" if prog > 0 else "🔴"
                mkt  = data.get('market_type', 'SPOT')
                with st.expander(
                    f"{icon} {p} [{mkt}] | P&L {prog:+.2f}% | Score: {data.get('score','N/A')}",
                    expanded=True
                ):
                    ca, cb, cc, cd = st.columns([2, 2, 2, 1])
                    ca.metric("ENTRÉE",     fmt(data['entry']))
                    cb.metric("PRIX LIVE",  fmt(cur_p), f"{prog:+.2f}%")
                    dist_tp = ((data.get('tp1', data.get('tp', cur_p)) - cur_p) / cur_p) * 100
                    cc.metric("Dist TP1",   f"{dist_tp:+.2f}%")
                    if cd.button("💰 VENDRE", key=f"sell_{p}"):
                        archive_position(p, data, cur_p, "MANUEL 💼")
                        del st.session_state['test_positions'][p]
                        save_data(DB_FILE, st.session_state['test_positions'])
                        st.rerun()
                    st.caption(
                        f"SL: {fmt(data['sl'])} ({data.get('risk_pct', '?'):.2f}%)  |  "
                        f"TP1: {fmt(data.get('tp1', data.get('tp')))}  |  "
                        f"TP2: {fmt(data.get('tp2'))}  |  "
                        f"Style: {data['style']}  |  Ouvert: {data.get('time_full','?')}"
                    )
            except Exception as e:
                st.warning(f"Erreur lecture {p}: {e}")
                continue
    else:
        st.info("Aucune position active.")

    # Historique
    st.write("---")
    with st.expander("📜 HISTORIQUE DÉTAILLÉ", expanded=False):
        if history:
            df_hist = pd.DataFrame(history)
            cols_ord = ["SYMBOLE","MARCHÉ","OUVERTURE","FERMETURE","ENTREE","SORTIE","PNL %","RAISON","STYLE"]
            cols_ok  = [c for c in cols_ord if c in df_hist.columns]
            st.dataframe(df_hist[cols_ok].iloc[::-1], use_container_width=True, height=400)

            # Mini chart PnL cumulé
            vals_chart = []
            for h in history:
                try: vals_chart.append(float(h.get('PNL %','0').replace('+','').replace('%','').strip()))
                except: pass
            if vals_chart:
                cumul = pd.Series(vals_chart).cumsum().tolist()
                st.line_chart(pd.DataFrame({"PnL cumulé (%)": cumul}))
        else:
            st.info("L'historique est vide.")
