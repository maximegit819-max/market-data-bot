import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text

st.set_page_config(page_title="Dashboard Structuration", layout="wide")

st.title("Screener Quantitatif & Analyse")

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
    """Récupère l'historique des prix ET les moyennes mobiles pour le graphique"""
    query = f"""
    SELECT 
        h.date_prix, 
        h.prix_cloture AS "Prix", 
        m.sma_6m AS "Moyenne 6M", 
        m.sma_1y AS "Moyenne 1Y"
    FROM historical_price h
    LEFT JOIN vue_moyennes_mobiles_standard m 
           ON h.ticker_yahoo = m.ticker_yahoo AND h.date_prix = m.date_prix
    WHERE h.ticker_yahoo = '{ticker}' 
    ORDER BY h.date_prix ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        df.set_index('date_prix', inplace=True)
        return df

@st.cache_data(ttl=3600)
def load_vol_term_structure(ticker):
    """Récupère les différents horizons de volatilité pour l'action"""
    query = f"""
    SELECT vol_1m_pct, vol_6m_pct, vol_1y_pct, vol_5y_pct
    FROM vue_volatilite_standard
    WHERE ticker_yahoo = '{ticker}'
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

@st.cache_data(ttl=3600)
def load_correlations(ticker):
    """Récupère le Top 5 et Flop 5 avec la volatilité associée"""
    query = f"""
    WITH correlations AS (
        SELECT actif_2 AS ticker, nom_actif_2 AS nom, corr_1y AS correlation
        FROM vue_correlation_standard WHERE actif_1 = '{ticker}' AND corr_1y IS NOT NULL
        UNION
        SELECT actif_1 AS ticker, nom_actif_1 AS nom, corr_1y AS correlation
        FROM vue_correlation_standard WHERE actif_2 = '{ticker}' AND corr_1y IS NOT NULL
    )
    SELECT 
        c.ticker AS "Ticker", 
        c.nom AS "Entreprise", 
        c.correlation AS "Corrélation (1 An)",
        v.vol_1y_pct AS "Volatilité 1Y (%)"
    FROM correlations c
    LEFT JOIN vue_volatilite_standard v ON c.ticker = v.ticker_yahoo
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        top_5 = df.nlargest(5, 'Corrélation (1 An)')
        bottom_5 = df.nsmallest(5, 'Corrélation (1 An)')
        return top_5, bottom_5

tab1, tab2 = st.tabs(["Screener Global Market", "Fiche Analyse par Action"])

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
        st.metric(label="Pire Drawdown (1Y)", value=pire_dd['Sous-Jacent'], delta=f"{pire_dd['Drawdown 1Y (%)']:.2f}%")

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
    liste_noms = df_screener['Sous-Jacent'].tolist()
    nom_choisi = st.selectbox("Rechercher une entreprise :", liste_noms)
    
    if nom_choisi:
        # On extrait la ligne spécifique à l'action choisie depuis le dataframe du screener
        infos_action = df_screener[df_screener['Sous-Jacent'] == nom_choisi].iloc[0]
        ticker_choisi = infos_action['Ticker']
        
        st.divider()
        
        # Bandeau de Synthèse (KPIs)
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("Prix Actuel", f"{infos_action['Prix Clôture']:.2f}", f"{infos_action['Perf 1J (%)']:.2f}%")
        kpi2.metric("Performance 1Y", f"{infos_action['Perf 1Y (%)']:.2f}%")
        kpi3.metric("Volatilité 1Y", f"{infos_action['Volatilité 1Y (%)']:.2f}%")
        kpi4.metric("Drawdown 1Y", f"{infos_action['Drawdown 1Y (%)']:.2f}%")
        
        st.divider()

        col_gauche, col_droite = st.columns([2, 1]) 
        
        with col_gauche:
            st.subheader("Historique et Tendance")
            df_historique = load_stock_history(ticker_choisi)
            st.line_chart(df_historique, color=["#0b57d0", "#f9ab00", "#146c2e"], height=400)
            
        with col_droite:
            # --- NOUVEAU : Histogramme de Term Structure de Volatilité ---
            st.subheader("Term Structure (Volatilité)")
            df_vol = load_vol_term_structure(ticker_choisi)
            
            if not df_vol.empty:
                # On formate les données
                horizons = ["1 Mois", "6 Mois", "1 An", "5 Ans"]
                vol_data = pd.DataFrame({
                    "Horizon": horizons,
                    "Volatilité (%)": [
                        df_vol.iloc[0]['vol_1m_pct'],
                        df_vol.iloc[0]['vol_6m_pct'],
                        df_vol.iloc[0]['vol_1y_pct'],
                        df_vol.iloc[0]['vol_5y_pct']
                    ]
                })
                
                # Création d'un graphique Plotly sur-mesure et professionnel
                fig = px.bar(
                    vol_data, 
                    x="Horizon", 
                    y="Volatilité (%)",
                    text="Volatilité (%)",
                    color_discrete_sequence=["#1e3a8a"] # Un bleu "Navy" professionnel et sobre
                )
                
                # Forçage de l'ordre chronologique exact et nettoyage du design
                fig.update_layout(
                    xaxis={'categoryorder':'array', 'categoryarray': horizons, 'showgrid': False, 'zeroline': False},
                    yaxis={'showticklabels': False, 'showgrid': False, 'zeroline': False}, # On cache l'axe Y pour un look minimaliste
                    xaxis_title=None,
                    yaxis_title=None,
                    margin=dict(l=0, r=0, t=30, b=0), # Marge haute augmentée pour que le texte respire
                    height=250,
                    plot_bgcolor="rgba(0,0,0,0)", # Fond transparent
                    paper_bgcolor="rgba(0,0,0,0)",
                    bargap=0.5 # C'est ici qu'on affine considérablement la largeur des barres !
                )
                
                fig.update_traces(
                    texttemplate='<b>%{text:.1f}%</b>', 
                    textposition='outside',
                    textfont=dict(size=14, color="#334155"), # Texte plus grand et gris ardoise
                    marker_line_width=0, # Design "flat" sans bordure
                    cliponaxis=False # Empêche que le texte de la barre la plus haute soit coupé
                )
                
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
        # On sort des colonnes "gauche/droite" pour prendre toute la largeur de l'écran
        st.divider()
        
        df_top5, df_bottom5 = load_correlations(ticker_choisi)
        
        col_corr, col_decorr = st.columns(2)
        
        with col_corr:
            st.subheader("Top 5 Corrélés (1Y)")
            st.dataframe(
                df_top5,
                column_config={
                    "Corrélation (1 An)": st.column_config.NumberColumn(format="%.2f"),
                    "Volatilité 1Y (%)": st.column_config.NumberColumn(format="%.2f %%")
                },
                use_container_width=True, hide_index=True
            )
            
        with col_decorr:
            st.subheader("Top 5 Décorrélés (1Y)")
            st.dataframe(
                df_bottom5,
                column_config={
                    "Corrélation (1 An)": st.column_config.NumberColumn(format="%.2f"),
                    "Volatilité 1Y (%)": st.column_config.NumberColumn(format="%.2f %%")
                },
                use_container_width=True, hide_index=True
            )
