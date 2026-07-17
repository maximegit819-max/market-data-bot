import os
import pandas as pd
from sqlalchemy import create_engine
import requests
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dotenv import load_dotenv

load_dotenv()
DB_URL = os.getenv("SUPABASE_DB_URL")
if DB_URL:
    if DB_URL.startswith("Postgresql://"):
        DB_URL = "postgresql://" + DB_URL[len("Postgresql://"):]
    elif DB_URL.startswith("postgres://"):
        DB_URL = "postgresql://" + DB_URL[len("postgres://"):]


def fetch_prices_via_chart_api(ticker, session):
    """Appelle directement l'API de graphique interactive de Yahoo Finance sur 5 jours."""
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{ticker}"
    params = {
        "range": "5d",
        "interval": "1d"
    }
    try:
        resp = session.get(url, params=params, timeout=10)
        if resp.status_code == 200:
            json_data = resp.json()
            result = json_data.get('chart', {}).get('result', [])
            if result and result[0]:
                timestamps = result[0].get('timestamp', [])
                indicators = result[0].get('indicators', {}).get('quote', [{}])[0]
                closes = indicators.get('close', [])
                
                if timestamps and closes:
                    dates = [pd.to_datetime(ts, unit='s').date() for ts in timestamps]
                    df = pd.DataFrame({
                        'date': dates,
                        'close': closes
                    }).dropna()
                    return df
    except Exception:
        pass
    return pd.DataFrame()

def fetch_ticker_data(ticker, name, session, existing_keys):
    """Télécharge, filtre les doublons récents et retourne le DataFrame prêt pour SQL."""
    time.sleep(0.1)
    df = fetch_prices_via_chart_api(ticker, session)
    if df.empty:
        return None, ticker
        
    dates_to_insert = []
    prices_to_insert = []
    for _, row in df.iterrows():
        date = row['date']
        price = row['close']
        if (ticker, date) not in existing_keys:
            dates_to_insert.append(date)
            prices_to_insert.append(price)
            
    if not dates_to_insert:
        return None, None
        
    df_ticker = pd.DataFrame({
        'ticker_yahoo': ticker,
        'nom_entreprise': name,
        'date_prix': dates_to_insert,
        'prix_cloture': pd.Series(prices_to_insert).round(4).values
    })
    return df_ticker, None

def main():
    print("[*] Démarrage de la mise à jour quotidienne...")
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/115.0.0.0 Safari/537.36'
    }
    session = requests.Session()
    session.headers.update(headers)
    
    engine = create_engine(DB_URL)
    
    # 1. Récupération dynamique de tous les tickers présents en base de données
    try:
        with engine.connect() as conn:
            df_db_tickers = pd.read_sql("SELECT DISTINCT ticker_yahoo, nom_entreprise FROM historical_price", conn)
        tickers_map = dict(zip(df_db_tickers['ticker_yahoo'], df_db_tickers['nom_entreprise']))
        print(f"[OK] {len(tickers_map)} tickers uniques à mettre à jour trouvés en base de données.")
    except Exception as e:
        print(f"[ERREUR] Impossible de charger les tickers depuis la base : {e}")
        return

    if not tickers_map:
        print("[INFO] Aucun ticker trouvé en base de données. Exécutez d'abord le script d'initialisation historique.")
        return

    # 2. Récupération des entrées des 10 derniers jours pour le filtre anti-doublons
    try:
        with engine.connect() as conn:
            existing = pd.read_sql(
                "SELECT ticker_yahoo, date_prix FROM historical_price WHERE date_prix >= CURRENT_DATE - INTERVAL '10 days'", 
                conn
            )
        existing_keys = set(zip(existing['ticker_yahoo'], existing['date_prix']))
        print(f"[OK] {len(existing_keys)} couples récents chargés depuis la base de données.")
    except Exception as e:
        existing_keys = set()
        print(f"[INFO] Aucun couple récent chargé (table vide) : {e}")

    # 3. Téléchargement en parallèle des cours sur 5 jours
    all_dfs = []
    failed_tickers = []
    
    print(f"[*] Téléchargement des cours pour {len(tickers_map)} tickers en parallèle (5 workers)...")
    with ThreadPoolExecutor(max_workers=5) as executor:
        futures = {
            executor.submit(fetch_ticker_data, ticker, name, session, existing_keys): ticker 
            for ticker, name in tickers_map.items()
        }
        
        completed = 0
        for future in as_completed(futures):
            ticker = futures[future]
            try:
                df_ticker, failed_ticker = future.result()
                if df_ticker is not None:
                    all_dfs.append(df_ticker)
                if failed_ticker is not None:
                    failed_tickers.append(failed_ticker)
            except Exception:
                failed_tickers.append(ticker)
                
            completed += 1
            if completed % 100 == 0 or completed == len(tickers_map):
                print(f"    -> Progression : {completed}/{len(tickers_map)} tickers traités...")

    # 4. Insertion en base de données des nouvelles lignes
    inserted_rows = 0
    if all_dfs:
        df_final = pd.concat(all_dfs, ignore_index=True)
        try:
            df_final.to_sql('historical_price', engine, if_exists='append', index=False)
            inserted_rows = len(df_final)
            print(f"[OK] Mise à jour terminée : {inserted_rows} nouvelles lignes ajoutées.")
        except Exception as e:
            print(f"[ERREUR] Échec de l'écriture SQL : {e}")
    else:
        print("[INFO] Déjà à jour. Aucun nouveau cours à insérer.")

    # 5. Résumé des échecs s'il y en a
    if failed_tickers:
        print(f"[AVERTISSEMENT] {len(failed_tickers)} tickers n'ont pas pu être mis à jour : {failed_tickers}")

if __name__ == "__main__":
    main()
