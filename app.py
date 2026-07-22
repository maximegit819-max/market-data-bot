import streamlit as st
import pandas as pd
import plotly.express as px
from sqlalchemy import create_engine, text
import datetime

st.set_page_config(page_title="Dashboard Structuration", layout="wide")

st.title("Screener Quantitatif & Analyse")

@st.cache_resource
def init_connection():
    db_url = st.secrets["SUPABASE_DB_URL"]
    if db_url and db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql://", 1)
    return create_engine(db_url)

engine = init_connection()

@st.cache_data(ttl="12h") 
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

@st.cache_data(ttl="12h")
def load_stock_history(ticker, date_debut, date_fin):
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
      AND h.date_prix >= '{date_debut}' 
      AND h.date_prix <= '{date_fin}'
    ORDER BY h.date_prix ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        df.set_index('date_prix', inplace=True)
        return df

@st.cache_data(ttl="12h")
def load_vol_term_structure(ticker):
    query = f"""
    SELECT vol_1m_pct, vol_6m_pct, vol_1y_pct, vol_5y_pct
    FROM vue_volatilite_standard
    WHERE ticker_yahoo = '{ticker}'
    """
    with engine.connect() as conn:
        return pd.read_sql(text(query), conn)

@st.cache_data(ttl=3600)
def load_correlations(ticker):
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

@st.cache_data(ttl="12h")
def load_comparison_data(ticker1, ticker2, nom1, nom2, date_debut, date_fin):
    """Récupère et normalise les prix de deux actions pour les comparer (Base 100)"""
    query = f"""
    SELECT date_prix, ticker_yahoo, prix_cloture
    FROM historical_price
    WHERE ticker_yahoo IN ('{ticker1}', '{ticker2}')
      AND date_prix >= '{date_debut}'
      AND date_prix <= '{date_fin}'
    ORDER BY date_prix ASC
    """
    with engine.connect() as conn:
        df = pd.read_sql(text(query), conn)
        if df.empty:
            return pd.DataFrame()
        
        # On pivote le tableau pour avoir les dates en index et les tickers en colonnes
        df_pivot = df.pivot(index='date_prix', columns='ticker_yahoo', values='prix_cloture')
        
        # On renomme les colonnes avec les vrais noms d'entreprises
        df_pivot.rename(columns={ticker1: nom1, ticker2: nom2}, inplace=True)
        
        # Le secret : On divise chaque ligne par la toute première ligne (la date de début) et on multiplie par 100
        df_normalized = (df_pivot / df_pivot.iloc[0]) * 100
        return df_normalized

# ==============================================================================
# INTERFACE UTILISATEUR
# ==============================================================================

tab1, tab2, tab3 = st.tabs(["Screener Global Market", "Fiche Analyse par Action", "Comparateur"])

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
    nom_choisi = st.selectbox("Rechercher une entreprise :", liste_noms, key="select_tab2")
    
    if nom_choisi:
        infos_action = df_screener[df_screener['Sous-Jacent'] == nom_choisi].iloc[0]
        ticker_choisi = infos_action['Ticker']
        
        st.divider()
        
        kpi1, kpi2, kpi3, kpi4 = st.columns(4)
        kpi1.metric("Prix Actuel", f"{infos_action['Prix Clôture']:.2f}", f"{infos_action['Perf 1J (%)']:.2f}%")
        kpi2.metric("Performance 1Y", f"{infos_action['Perf 1Y (%)']:.2f}%")
        kpi3.metric("Volatilité 1Y", f"{infos_action['Volatilité 1Y (%)']:.2f}%")
        kpi4.metric("Drawdown 1Y", f"{infos_action['Drawdown 1Y (%)']:.2f}%")
        
        st.divider()

        col_gauche, col_droite = st.columns([2, 1]) 
        
        with col_gauche:
            col_titre, col_calendrier = st.columns([1, 1])
            with col_titre:
                st.subheader("Historique et Tendance")
            with col_calendrier:
                date_fin_defaut = datetime.date.today()
                date_debut_defaut = date_fin_defaut - datetime.timedelta(days=365)
                
                dates = st.date_input(
                    "Période d'analyse :",
                    value=(date_debut_defaut, date_fin_defaut),
                    max_value=date_fin_defaut,
                    key="dates_tab2"
                )
            
            if len(dates) == 2:
                df_historique = load_stock_history(ticker_choisi, dates[0], dates[1])
                st.line_chart(df_historique, color=["#0b57d0", "#f9ab00", "#146c2e"], height=400)
            else:
                st.info("⏳ Veuillez sélectionner une date de fin sur le calendrier.")
            
        with col_droite:
            st.subheader("Structure (Vol)")
            df_vol = load_vol_term_structure(ticker_choisi)
            
            if not df_vol.empty:
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
                
                fig = px.bar(
                    vol_data, 
                    x="Horizon", 
                    y="Volatilité (%)",
                    text="Volatilité (%)",
                    color_discrete_sequence=["#1e3a8a"] 
                )
                
                fig.update_layout(
                    xaxis={'categoryorder':'array', 'categoryarray': horizons, 'showgrid': False, 'zeroline': False},
                    yaxis={'showticklabels': False, 'showgrid': False, 'zeroline': False, 'visible': False},
                    xaxis_title=None,
                    yaxis_title=None,
                    margin=dict(l=0, r=0, t=30, b=0),
                    height=250,
                    plot_bgcolor="rgba(0,0,0,0)", 
                    paper_bgcolor="rgba(0,0,0,0)",
                    bargap=0.5 
                )
                
                fig.update_traces(
                    texttemplate='<b>%{text:.1f}%</b>', 
                    textposition='outside',
                    textfont=dict(size=14, color="#475569"), 
                    marker_line_width=0, 
                    cliponaxis=False 
                )
                
                st.plotly_chart(fig, use_container_width=True, config={'displayModeBar': False})
        
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

with tab3:
    st.subheader("Comparateur de Performance (Base 100)")
    
    col_sel1, col_sel2, col_dates = st.columns([1.5, 1.5, 1])
    
    with col_sel1:
        nom1 = st.selectbox("Actif n°1 :", liste_noms, index=0, key="select_comp1")
    with col_sel2:
        nom2 = st.selectbox("Actif n°2 :", liste_noms, index=1 if len(liste_noms) > 1 else 0, key="select_comp2")
    with col_dates:
        date_fin_comp = datetime.date.today()
        date_debut_comp = date_fin_comp - datetime.timedelta(days=365)
        dates_comp = st.date_input(
            "Période :",
            value=(date_debut_comp, date_fin_comp),
            max_value=date_fin_comp,
            key="dates_tab3"
        )
        
    if nom1 and nom2 and len(dates_comp) == 2:
        ticker1 = df_screener[df_screener['Sous-Jacent'] == nom1].iloc[0]['Ticker']
        ticker2 = df_screener[df_screener['Sous-Jacent'] == nom2].iloc[0]['Ticker']
        
        df_comparison = load_comparison_data(ticker1, ticker2, nom1, nom2, dates_comp[0], dates_comp[1])
        
        if not df_comparison.empty:
            st.line_chart(df_comparison, color=["#1e3a8a", "#f9ab00"], height=450)
            
        else:
            st.warning("Aucune donnée commune trouvée pour cette période.")
