# fetch_bhavcopy_one.py
# Usage: python fetch_bhavcopy_one.py
# Downloads NSE equity bhavcopy for 08-Aug-2025, filters LTFOODS (EQ), saves to SQLite.

import io, zipfile, sys
from datetime import datetime
import pandas as pd
import requests
from urllib3.util.retry import Retry
from requests.adapters import HTTPAdapter
from sqlalchemy import create_engine, types

# ---- Config (change if needed) ----
SYMBOL = "LTFOODS"
DATE_STR = "08-08-2025"  # DD-MM-YYYY
DB_PATH = "eod_prices.db"
TABLE_NAME = "eod_prices"
# -----------------------------------

def nse_session():
    s = requests.Session()
    s.headers.update({
        "User-Agent": "Mozilla/5.0",
        "Accept": "*/*",
        "Referer": "https://www.nseindia.com/",
        "Connection": "keep-alive",
    })
    retry = Retry(total=5, backoff_factor=0.7,
                  status_forcelist=[429,500,502,503,504],
                  allowed_methods=["GET"])
    s.mount("https://", HTTPAdapter(max_retries=retry))
    return s

def build_urls(d: datetime):
    dd   = d.strftime("%d")
    monU = d.strftime("%b").upper()   # AUG
    yyyy = d.strftime("%Y")           # 2025
    fname = f"cm{dd}{monU}{yyyy}bhav.csv.zip"
    url = f"https://archives.nseindia.com/content/historical/EQUITIES/{yyyy}/{monU}/{fname}"
    return [url], fname

def download_zip(s: requests.Session, urls):
    # No warm-up needed for archives host
    last_err = None
    for url in urls:
        try:
            r = s.get(url, timeout=30)
            r.raise_for_status()
            # ZIP magic bytes "PK"
            if not (r.headers.get("Content-Type","").lower().find("zip") != -1 or r.content.startswith(b"PK")):
                raise ValueError("Response not a ZIP file")
            return io.BytesIO(r.content)
        except Exception as e:
            last_err = e
    raise RuntimeError(f"Failed to download after {len(urls)} URLs. Last error: {last_err}")
def parse_csv_from_zip(zbytes: io.BytesIO):
    with zipfile.ZipFile(zbytes) as zf:
        # Expect exactly one CSV inside
        names = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not names:
            raise RuntimeError("ZIP did not contain a CSV")
        with zf.open(names[0]) as f:
            df = pd.read_csv(f)
    return df

def filter_symbol(df: pd.DataFrame, symbol: str):
    # Standard NSE columns: SYMBOL,SERIES,OPEN,HIGH,LOW,CLOSE,LAST,PREVCLOSE,TOTTRDQTY,TOTTRDVAL,TIMESTAMP,TOTALTRADES,ISIN
    df = df.rename(columns=str.upper)
    mask = (df["SYMBOL"].str.upper() == symbol.upper())
    # Prefer EQ series for regular trading
    if "SERIES" in df.columns:
        df_eq = df[mask & (df["SERIES"] == "EQ")]
        if not df_eq.empty:
            return df_eq
    return df[mask]

def to_sqlite(df: pd.DataFrame, db_path: str, table: str):
    # Normalize columns for our table
    cols = {c.upper(): c for c in df.columns}
    df = df.rename(columns=str.upper)

    keep = [
        "SYMBOL","SERIES","OPEN","HIGH","LOW","CLOSE",
        "TOTTRDQTY","TOTTRDVAL","TIMESTAMP","TOTALTRADES","ISIN"
    ]
    for k in keep:
        if k not in df.columns:
            df[k] = None

    out = df[keep].copy()
    # Standardize date
    out["date"] = pd.to_datetime(out["TIMESTAMP"], format="%d-%b-%Y", errors="coerce").dt.date

    # Final schema map
    out = out.rename(columns={
        "TOTTRDQTY": "volume",
        "TOTTRDVAL": "turnover",
    })

    engine = create_engine(f"sqlite:///{db_path}")

    dtypes = {
        "SYMBOL": types.String(32),
        "SERIES": types.String(8),
        "OPEN": types.Float(),
        "HIGH": types.Float(),
        "LOW": types.Float(),
        "CLOSE": types.Float(),
        "volume": types.BigInteger(),
        "turnover": types.Float(),
        "TOTALTRADES": types.BigInteger(),
        "ISIN": types.String(16),
    }

    out.to_sql(table, con=engine, if_exists="append", index=False, dtype=dtypes)
    return len(out)

def main():
    target_date = datetime.strptime(DATE_STR, "%d-%m-%Y")
    urls, fname = build_urls(target_date)
    print(f"Downloading: {fname} ...")
    s = nse_session()
    zbytes = download_zip(s, urls)
    df = parse_csv_from_zip(zbytes)
    row = filter_symbol(df, SYMBOL)

    if row.empty:
        print(f"No data found for {SYMBOL} on {DATE_STR}. (Possibly holiday or different series.)")
        sys.exit(1)

    n = to_sqlite(row, DB_PATH, TABLE_NAME)
    print(f"Saved {n} row(s) for {SYMBOL} on {DATE_STR} into SQLite → {DB_PATH}, table '{TABLE_NAME}'.")

if __name__ == "__main__":
    main()
