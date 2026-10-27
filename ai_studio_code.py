import datetime
import io
import time
import pandas as pd
import requests
import streamlit as st
import yfinance as yf

# ----------------- Page Config -----------------
st.set_page_config(
    page_title="NSE Live Promoter Screener", page_icon="📈", layout="wide"
)

st.title("📈 NSE Live Promoter Buying Screener")
st.caption(
    "Verified Filters: 3M Buy ≥ ₹1 Cr | Promoter > 50% | 0.00% Pledge | Positive EPS | No Sales"
)

# ----------------- Robust NSE Headers -----------------
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML,"
        " like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "Referer": (
        "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading"
    ),
}


def get_nse_data(from_date, to_date):
    """NSE API પરથી ડેટા લાવવા માટેનું સુરક્ષિત ફંક્શન"""
    session = requests.Session()
    session.headers.update(HEADERS)
    try:
        # First request to get cookies
        session.get(
            "https://www.nseindia.com/companies-listing/corporate-filings-insider-trading",
            timeout=10,
        )
        time.sleep(1)

        url = f"https://www.nseindia.com/api/corporates-pit?index=equities&from_date={from_date}&to_date={to_date}"
        response = session.get(url, timeout=15)

        if response.status_code == 200:
            json_data = response.json()
            if "data" in json_data and len(json_data["data"]) > 0:
                return pd.DataFrame(json_data["data"])
    except Exception as e:
        st.error(f"NSE ડેટા લાવવામાં ભૂલ થઈ: {e}")
    return pd.DataFrame()


# ----------------- Main Screening Logic -----------------
def run_screening():
    today = datetime.date.today()
    date_3m_ago = today - datetime.timedelta(days=90)
    date_6m_ago = today - datetime.timedelta(days=180)

    from_date_str = date_6m_ago.strftime("%d-%m-%Y")
    to_date_str = today.strftime("%d-%m-%Y")

    pit_df = get_nse_data(from_date_str, to_date_str)

    if pit_df.empty:
        st.warning(
            "NSE API તરફથી હાલ કોઈ ડેટા મળ્યો નથી (અથવા ક્લાઉડ સર્વર લિમિટ છે). કૃપા કરીને ૧-૨ મિનિટ પછી ફરી પ્રયાસ કરો."
        )
        return pd.DataFrame()

    # 1. KeyError થી બચવા માટે બધી જ સંભવિત કોલમ ચેક કરવી
    expected_cols = {
        "symbol": "",
        "secVal": 0,
        "secAcq": 0,
        "date": "",
        "personCategory": "",
        "acqMode": "",
        "tdpTransactionType": "",
        "tdpAcqType": "",
    }
    for col, default_val in expected_cols.items():
        if col not in pit_df.columns:
            pit_df[col] = default_val

    # Types કન્વર્ઝન
    pit_df["secVal"] = pd.to_numeric(pit_df["secVal"], errors="coerce").fillna(
        0
    )
    pit_df["secAcq"] = pd.to_numeric(pit_df["secAcq"], errors="coerce").fillna(
        0
    )
    pit_df["parsed_date"] = pd.to_datetime(pit_df["date"], errors="coerce")

    # માત્ર Promoter & Promoter Group
    promoter_roles = [
        "Promoter",
        "Promoter Group",
        "Promoters",
        "PROMOTER",
        "PROMOTER GROUP",
    ]
    pit_df = pit_df[
        pit_df["personCategory"].astype(str).str.strip().isin(promoter_roles)
    ]

    # નિયમ ૩: પ્રમોટરે કોઈ શેર વેચેલા ન હોવા જોઈએ (No Sale Filter)
    sale_mask = (
        pit_df["acqMode"].astype(str).str.contains("Sale|Sell", case=False)
        | pit_df["tdpTransactionType"]
        .astype(str)
        .str.contains("Sale|Sell", case=False)
        | pit_df["tdpAcqType"]
        .astype(str)
        .str.contains("Sale|Sell", case=False)
    )
    sold_symbols = pit_df[sale_mask]["symbol"].unique()

    # વેચાણ કરેલ શેર દૂર કરવા
    valid_buyers = pit_df[~pit_df["symbol"].isin(sold_symbols)]

    # માત્ર માર્કેટ પર્ચેઝ (Market Purchase)
    valid_buyers = valid_buyers[
        valid_buyers["acqMode"].astype(str).str.contains("Market", case=False)
    ]

    # નિયમ ૧: છેલ્લા ૩ મહિનામાં ₹1 કરોડ કે વધુ ખરીદી
    df_3m = valid_buyers[
        valid_buyers["parsed_date"] >= pd.Timestamp(date_3m_ago)
    ]
    buy_totals = df_3m.groupby("symbol")["secVal"].sum()
    qualified_symbols = buy_totals[buy_totals >= 10000000].index.tolist()

    if not qualified_symbols:
        st.info(
            "છેલ્લા ૩ મહિનામાં ₹૧ કરોડથી વધુ Open Market ખરીદી ધરાવતો કોઈ શેર મળ્યો નથી."
        )
        return pd.DataFrame()

    results = []
    progress_bar = st.progress(0)
    status_text = st.empty()

    for i, sym in enumerate(qualified_symbols):
        status_text.text(f"વેરિફિકેશન ચાલુ છે ({i+1}/{len(qualified_symbols)}): {sym}...")
        progress_bar.progress((i + 1) / len(qualified_symbols))

        try:
            # 6 મહિનાની Average Buy Price & Last Purchase Date (નિયમ ૬ અને ૭)
            sym_trades = valid_buyers[valid_buyers["symbol"] == sym]
            total_val = sym_trades["secVal"].sum()
            total_qty = sym_trades["secAcq"].sum()
            avg_price = (
                round(total_val / total_qty, 2) if total_qty > 0 else 0.0
            )

            last_dt = (
                sym_trades["parsed_date"].max().strftime("%d-%b-%Y")
                if not sym_trades["parsed_date"].isna().all()
                else "N/A"
            )
            buy_cr = round(buy_totals[sym] / 10000000, 2)

            # Yahoo Finance થી Shareholding, Pledge & EPS ચેક
            ticker = yf.Ticker(f"{sym}.NS")
            info = ticker.info

            # નિયમ ૨: Promoter Holding > 50%
            promoter_pct = (info.get("heldPercentInsiders") or 0) * 100
            if promoter_pct <= 50:
                continue

            # નિયમ ૪: 0.01% પણ Pledged ન હોવું જોઈએ
            pledge_pct = info.get("sharesPercentSharesOutPledged") or 0
            if pledge_pct > 0.0001:
                continue

            # નિયમ ૫: EPS Positive હોવો જોઈએ
            eps = info.get("trailingEps")
            if eps is None or eps <= 0:
                continue

            results.append(
                {
                    "Symbol": sym,
                    "Company Name": info.get("shortName", sym),
                    "Promoter Holding": f"{round(promoter_pct, 2)}%",
                    "Pledged": "0.00%",
                    "TTM EPS": round(eps, 2),
                    "3M Buy (₹ Cr)": f"₹ {buy_cr} Cr",
                    "6M Avg Buy Price": f"₹ {avg_price}",
                    "Last Buy Date": last_dt,
                }
            )
            time.sleep(0.3)
        except Exception:
            continue

    status_text.empty()
    progress_bar.empty()
    return pd.DataFrame(results)


# ----------------- UI -----------------
st.sidebar.header("કંટ્રોલ પેનલ")
if st.sidebar.button("🔍 Run Live Scan Now", use_container_width=True):
    with st.spinner("NSE સર્વર સાથે કનેક્ટ થઈ રહ્યું છે..."):
        df_result = run_screening()

        if not df_result.empty:
            st.success(f"✅ {len(df_result)} લાયક શેરો મળી આવ્યા!")
            st.dataframe(df_result, use_container_width=True)

            # Excel Download
            buf = io.BytesIO()
            with pd.ExcelWriter(buf, engine="openpyxl") as w:
                df_result.to_excel(w, index=False, sheet_name="Filtered_Stocks")
            st.download_button(
                "📥 Download Excel",
                buf.getvalue(),
                file_name=f"Promoter_Buys_{datetime.date.today()}.xlsx",
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        else:
            st.info("બધા જ નિયમો મેચ થતા હોય તેવા કોઈ શેર મળ્યા નથી.")
else:
    st.info("ડાબી બાજુથી **'Run Live Scan Now'** બટન ક્લિક કરો.")