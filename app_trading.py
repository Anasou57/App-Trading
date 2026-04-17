if st.button("🚀 LANCER SCAN"):
            with st.status("Analyse en cours..."):
                tickers = exchange.fetch_tickers()
                all_analyzed = []
                
                # On analyse les 50 plus grosses paires
                potential = [s for s in tickers if s.endswith('/USDT')]
                # (Tri par volume ici...)

                for p in potential[:50]:
                    ana = get_market_analysis(p, mode_actuel)
                    if ana:
                        all_analyzed.append(ana)
                
                # AU LIEU DE FILTRER STRICTEMENT, ON TRIE
                if "RANGE" in mode_actuel:
                    # On prend les ADX les plus bas (les plus stables)
                    all_analyzed.sort(key=lambda x: x['adx'])
                else:
                    # On prend les ADX les plus hauts (ceux qui bougent le plus)
                    all_analyzed.sort(key=lambda x: x['adx'], reverse=True)
                
                # On affiche les 15 meilleurs, peu importe leur score absolu
                st.session_state['scan_res'] = [x['symbol'] for x in all_analyzed[:15]]
