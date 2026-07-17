import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Dashboard Structuration", layout="wide", page_icon="📈")

st.title("Screener Quantitatif & Analyse")
st.markdown("Données de marché en direct de **Supabase**.")

@st.cache_resource
def init_connection():
    db_url = st.secrets["SUPABASE_DB_URL"]
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return create_engine(db_url)

engine = init_connection()

@st.cache_data(ttl=3600) 
def load_screener_data():
    query = """
    WITH latest_daily AS (
        SELECT * FROM (
            SELECT ticker_yahoo, nom_entreprise, date_prix, prix_cloture,
                   ((prix_cloture / LAG(prix_cloture) OVER(PARTITION BY ticker_yahoo ORDER BY date_prix)) - 1) * 100 AS perf_1j_pct,
                   ROW_NUMBER() OVER(PARTITION BY ticker_yahoo ORDER BY date_prix DESC) as rn
            FROM historical_price
        ) sub WHERE rn = 1
    )
    SELECT 
        l.nom_entreprise AS "Sous-Jacent",
        l.ticker_yahoo AS "Ticker",
        l.prix_cloture AS "Prix Clôture",
        l.perf_1j_pct AS "Perf 1J (%)",
        p.perf_1m_pct AS "Perf 1M (%)",
        p.perf_1y_pct AS "Perf 1Y (%)",
        v.vol_1y_pct AS "Volatilité 1Y (%)",
        d.dd_1y_pct AS "Drawdown 1Y (%)"
    FROM latest_daily l
    LEFT JOIN vue_performances_standard p ON l.ticker_yahoo = p.ticker_yahoo
    LEFT JOIN vue_volatilite_standard v ON l.ticker_yahoo = v.ticker_yahoo
    LEFT JOIN vue_drawdowns_actuels_standard d ON l.ticker_yahoo = d.ticker_yahoo
    WHERE l.perf_1j_pct IS NOT NULL
    ORDER BY l.perf_1j_pct DESC;
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

@st.cache_data(ttl=3600)
def load_stock_history(ticker):
    """Récupère l'historique des prix pour le graphique"""
    query = f"SELECT date_prix, prix_cloture FROM historical_price WHERE ticker_yahoo = '{ticker}' ORDER BY date_prix ASC"
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        df.set_index('date_prix', inplace=True)
        return df

@st.cache_data(ttl=3600)
def load_correlations(ticker):
    """Récupère le Top 10 des actions les plus corrélées"""
    query = f"""
    SELECT actif_2 AS "Ticker", nom_actif_2 AS "Entreprise", corr_1y AS "Corrélation (1 An)"
    FROM vue_correlation_standard WHERE actif_1 = '{ticker}' AND corr_1y IS NOT NULL
    UNION
    SELECT actif_1 AS "Ticker", nom_actif_1 AS "Entreprise", corr_1y AS "Corrélation (1 An)"
    FROM vue_correlation_standard WHERE actif_2 = '{ticker}' AND corr_1y IS NOT NULL
    ORDER BY "Corrélation (1 An)" DESC
    LIMIT 10;
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

tab1, tab2 = st.tabs(["📊 Screener Global Market", "🔍 Fiche Analyse par Action"])

with tab1:
    with st.spinner("Récupération des données marché..."):
        df_screener = load_screener_data()

    col1, col2, col3 = st.columns(3)
    with col1:
        st.metric(label="Plus forte hausse (1J)", value=df_screener.iloc[0]['Sous-Jacent'], delta=f"{df_screener.iloc[0]['Perf 1J (%)']:.2f}%")
    with col2:
        st.metric(label="Plus forte baisse (1J)", value=df_screener.iloc[-1]['Sous-Jacent'], delta=f"{df_screener.iloc[-1]['Perf 1J (%)']:.2f}%")
    with col3:
        pire_dd = df_screener.sort_values(by="Drawdown 1Y (%)").iloc[0]
        st.metric(label="Plus gros krach actuel (1Y)", value=pire_dd['Sous-Jacent'], delta=f"{pire_dd['Drawdown 1Y (%)']:.2f}%")

    st.dataframe(
        df_screener,
        column_config={
            "Prix Clôture": st.column_config.NumberColumn(format="%.2f"),
            "Perf 1J (%)": st.column_config.NumberColumn(format="%.2f %%"),
            "Perf 1M (%)": st.column_config.NumberColumn(format="%.2f %%"),
            "Perf 1Y (%)": st.column_config.NumberColumn(format="%.2f %%"),
            "Volatilité 1Y (%)": st.column_config.NumberColumn(format="%.2f %%"),
            "Drawdown 1Y (%)": st.column_config.NumberColumn(format="%.2f %%"),
        },
        use_container_width=True, hide_index=True, height=500
    )

with tab2:
    liste_tickers = df_screener['Ticker'].tolist()
    ticker_choisi = st.selectbox("Sélectionnez un sous-jacent à analyser :", liste_tickers)
    
    if ticker_choisi:
        st.divider()
        col_gauche, col_droite = st.columns([2, 1]) 
        
        with col_gauche:
            st.subheader(f"📈 Historique des prix - {ticker_choisi}")
            df_historique = load_stock_history(ticker_choisi)
            st.line_chart(df_historique['prix_cloture'], color="#0b57d0", height=400)
            
        with col_droite:
            st.subheader("🔗 Top 10 Corrélations (1Y)")
            st.markdown("Idéal pour trouver un *Worst-Of*.")
            df_corr = load_correlations(ticker_choisi)
            st.dataframe(
                df_corr,
                column_config={"Corrélation (1 An)": st.column_config.NumberColumn(format="%.2f")},
                use_container_width=True, hide_index=True, height=400
            )
