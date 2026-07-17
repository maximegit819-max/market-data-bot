import streamlit as st
import pandas as pd
from sqlalchemy import create_engine, text
import os
from dotenv import load_dotenv

# 1. Configuration de la page Web (doit être la première commande)
st.set_page_config(page_title="Screener Structuration", layout="wide", page_icon="")

# En-tête de l'application
st.title("Screener Quantitatif")
st.markdown("Classement dynamique des actifs du marché. Les données proviennent en direct de **Supabase**.")

# 2. Gestion intelligente de la connexion
# @st.cache_resource permet de ne pas se reconnecter à chaque fois qu'on clique sur un bouton
@st.cache_resource
def init_connection():
    # En local, on cherche le fichier .env. Dans le Cloud, on cherchera st.secrets
    try:
        db_url = st.secrets["SUPABASE_DB_URL"]
    except:
        load_dotenv()
        db_url = os.getenv("SUPABASE_DB_URL")
        
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
        
    return create_engine(db_url)

engine = init_connection()

# 3. Récupération des données avec le "Master JOIN" SQL
# @st.cache_data garde les données en mémoire (évite de surcharger la base de données)
@st.cache_data(ttl=3600) 
def load_data():
    query = """
    WITH daily_perf AS (
        -- A. On calcule la performance de la veille (Perf 1J)
        SELECT ticker_yahoo, nom_entreprise, date_prix,
               ((prix_cloture / LAG(prix_cloture) OVER(PARTITION BY ticker_yahoo ORDER BY date_prix)) - 1) * 100 AS perf_1j_pct
        FROM historical_price
    ),
    latest_daily AS (
        -- B. On isole uniquement la date la plus récente pour chaque action
        SELECT * FROM (
            SELECT *, ROW_NUMBER() OVER(PARTITION BY ticker_yahoo ORDER BY date_prix DESC) as rn
            FROM daily_perf
        ) sub WHERE rn = 1
    )
    -- C. On rassemble TOUTES nos vues existantes en un seul super-tableau
    SELECT 
        l.nom_entreprise AS "Sous-Jacent",
        l.ticker_yahoo AS "Ticker",
        l.perf_1j_pct AS "Perf 1J (%)",
        p.perf_1m_pct AS "Perf 1M (%)",
        p.perf_6m_pct AS "Perf 6M (%)",
        p.perf_1y_pct AS "Perf 1Y (%)",
        v.vol_1m_pct AS "Volatilité 1M (%)",
        v.vol_6m_pct AS "Volatilité 6M (%)",
        v.vol_1y_pct AS "Volatilité 1Y (%)",
        d.dd_1y_pct AS "Drawdown 1Y (%)",
        m.sma_1y AS "Moy. Mobile 1Y"
    FROM latest_daily l
    LEFT JOIN vue_performances_standard p ON l.ticker_yahoo = p.ticker_yahoo
    LEFT JOIN vue_volatilite_standard v ON l.ticker_yahoo = v.ticker_yahoo
    LEFT JOIN vue_drawdowns_actuels_standard d ON l.ticker_yahoo = d.ticker_yahoo
    LEFT JOIN vue_moyennes_mobiles_standard m ON l.ticker_yahoo = m.ticker_yahoo 
        AND m.date_prix = l.date_prix
    WHERE l.perf_1j_pct IS NOT NULL
    ORDER BY l.perf_1j_pct DESC;
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

# 4. Affichage de l'interface
# On utilise un "spinner" pendant que ça charge pour faire professionnel
with st.spinner("Connexion à Supabase et exécution des modèles mathématiques..."):
    df = load_data()

# Affichage des métriques clés en haut
st.subheader(f"Données marché du {df['Sous-Jacent'].count()} actifs")

col1, col2, col3 = st.columns(3)
with col1:
    st.metric(label="Plus forte hausse (1J)", value=df.iloc[0]['Sous-Jacent'], delta=f"{df.iloc[0]['Perf 1J (%)']:.2f}%")
with col2:
    st.metric(label="Plus forte baisse (1J)", value=df.iloc[-1]['Sous-Jacent'], delta=f"{df.iloc[-1]['Perf 1J (%)']:.2f}%")
with col3:
    pire_dd = df.sort_values(by="Drawdown 1Y (%)").iloc[0]
    st.metric(label="Plus gros krach (1Y)", value=pire_dd['Sous-Jacent'], delta=f"{pire_dd['Drawdown 1Y (%)']:.2f}%")

st.divider()

# 5. Affichage du tableau de bord principal avec formatage
st.dataframe(
    df,
    column_config={
        "Perf 1J (%)": st.column_config.NumberColumn(format="%.2f %%"),
        "Perf 1Y (%)": st.column_config.NumberColumn(format="%.2f %%"),
        "Volatilité 1Y (%)": st.column_config.NumberColumn(format="%.2f %%"),
        "Drawdown 1Y (%)": st.column_config.NumberColumn(format="%.2f %%"),
        "Moy. Mobile 1Y": st.column_config.NumberColumn(format="%.2f"),
    },
    use_container_width=True, # Prend toute la largeur de l'écran
    hide_index=True,          # Cache la numérotation moche de Pandas 0,1,2,3...
    height=600                # Hauteur du tableau
)

st.caption("💡 Astuce : Cliquez sur l'en-tête d'une colonne (ex: Volatilité) pour trier le tableau. Vous pouvez aussi utiliser l'icône de recherche.")