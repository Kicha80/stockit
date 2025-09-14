from __future__ import annotations

import os
import glob
import sys
import subprocess
import logging
from typing import Optional, List
from datetime import datetime
import numpy as np
import pandas as pd
import feedparser
from pathlib import Path
from uploads.P2 import run_option_chain_export,generate_levels_from_latest # <-- import your wrapper

from flask import (
    Flask, render_template, request, redirect, url_for, flash,
    jsonify, send_from_directory, Response
)
import requests

# ---------------------------------------------------
# App setup
# ---------------------------------------------------
app = Flask(__name__, static_url_path='/static')
@app.route("/refresh-oc")
def refresh_oc():
    exp_n = request.args.get("expiry_n")
    exp_b = request.args.get("expiry_b")
    try:
        result = run_option_chain_export(out_dir=Path(app.config["UPLOAD_FOLDER"]))
        generate_levels_from_latest(out_dir=Path(app.config["UPLOAD_FOLDER"]))
        flash(f"Refreshed {len(result['written'])} file(s) + levels file")
    except Exception as e:
        flash(f"Refresh failed: {e}")
    return redirect(url_for("option_chain", expiry_n=exp_n, expiry_b=exp_b))
    
app.secret_key = os.environ.get("FLASK_SECRET_KEY", "devkey-change-me")

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

app.config["UPLOAD_FOLDER"] = UPLOAD_DIR
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024 * 1024

logging.basicConfig(level=logging.INFO)

# ---------------------------------------------------
# Canonical columns (exact order you want)
# ---------------------------------------------------
OC_COLUMNS: List[str] = [
    "IV_CALL", "LTP_CALL", "VOLUME_CALL", "OI_CALL", "CHG IN OI_CALL",
    "STRIKE",
    "CHG IN OI_PUT", "OI_PUT", "VOLUME_PUT", "LTP_PUT", "IV_PUT",
    "EXPIRY DATE",
]
COUNT_COLS = [
    "VOLUME_CALL", "OI_CALL", "CHG IN OI_CALL",
    "CHG IN OI_PUT", "OI_PUT", "VOLUME_PUT",
]

# ---------------------------------------------------
# Landing-page helpers (kept simple)
# ---------------------------------------------------
def load_stock_symbols():
    try:
        df = pd.read_excel(os.path.join(BASE_DIR, "stock_names.xlsx"))
        return df["Stock"].dropna().tolist()
    except Exception as e:
        app.logger.warning(f"load_stock_symbols: {e}")
        return []

def load_industries():
    try:
        df = pd.read_excel(os.path.join(BASE_DIR, "Stocks_top_Perf.xlsx"))
        return df["Industry"].dropna().unique().tolist()
    except Exception as e:
        app.logger.warning(f"load_industries: {e}")
        return []

def fetch_news_from_rss():
    try:
        feed_url = "https://economictimes.indiatimes.com/rssfeedsdefault.cms"
        feed = feedparser.parse(feed_url)
        return [{"title": e.title, "link": e.link} for e in feed.entries[:10]]
    except Exception as e:
        app.logger.warning(f"fetch_news_from_rss: {e}")
        return []

def read_underlying(sym: str) -> Optional[float]:
    p = os.path.join(app.config["UPLOAD_FOLDER"], f"{sym}_underlying.txt")
    try:
        with open(p, "r", encoding="utf-8") as f:
            return float(f.read().strip())
    except Exception:
        return None
# ---------------------------------------------------
# Option-chain header normalization
# ---------------------------------------------------
def _canonical(col: str) -> str:
    """
    Map raw header variations to canonical names.
    Handles 'Unnamed', spaces, hyphens, CE/PE, etc.
    """
    t = str(col).strip()
    if t.startswith("Unnamed"):
        return ""  # drop later

    u = t.upper()
    for ch in "()_-":
        u = u.replace(ch, " ")
    u = " ".join(u.split())

    if "EXPIRY" in u:
        return "EXPIRY DATE"
    if "STRIKE" in u:
        return "STRIKE"

    side = None
    if "CALL" in u or u.endswith(" CE") or u.endswith("CE"):
        side = "CALL"
    elif "PUT" in u or u.endswith(" PE") or u.endswith("PE"):
        side = "PUT"

    if side:
        if "CHG" in u and "OI" in u:
            return f"CHG IN OI_{side}"
        if u.startswith("OI") or " OI" in u:
            return f"OI_{side}"
        if "VOL" in u:
            return f"VOLUME_{side}"
        if "LTP" in u:
            return f"LTP_{side}"
        if u.startswith("IV") or " IV" in u:
            return f"IV_{side}"

    return t  # fallback

def align_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Drop 'Unnamed' columns, map to canonical names, ensure OC_COLUMNS exist,
    and return exactly in OC_COLUMNS order.
    """
    # drop unnamed & all-null cols (mask aligned to columns)
    mask = ~df.columns.astype(str).str.startswith("Unnamed")
    df = df.loc[:, mask]
    df = df.dropna(axis=1, how="all")

    df = df.rename(columns={c: _canonical(c) for c in df.columns})
    # remove any blank-mapped columns (from Unnamed)
    df = df.loc[:, [c for c in df.columns if c]]

    for c in OC_COLUMNS:
        if c not in df.columns:
            df[c] = pd.NA

    return df[OC_COLUMNS].copy()

def _detect_header_row(path: str) -> int:
    """
    Pick the row that contains the REAL column names (IV_CALL, LTP_CALL, ...).
    If a 'CALL/STRIKE/PUT' band is present one row above, we skip it and use
    the next row.
    """
    raw = pd.read_excel(path, header=None, engine="openpyxl")
    n = min(10, len(raw))

    def tokens(i):
        return [str(x).strip().upper() for x in raw.iloc[i].tolist()]

    # "fine" headers we expect on the real header row
    fine = {
        "IV_CALL","LTP_CALL","VOLUME_CALL","OI_CALL","CHG IN OI_CALL",
        "IV_PUT","LTP_PUT","VOLUME_PUT","OI_PUT","CHG IN OI_PUT",
        "EXPIRY DATE"
    }

    # Pass 1: find a row that contains at least 3 fine headers
    for i in range(n):
        rowset = set(tokens(i))
        hits = len(rowset & fine)
        if hits >= 3:
            return i

    # Pass 2: if a row has STRIKE but no fine headers, try the NEXT row
    for i in range(n - 1):
        row_now = set(tokens(i))
        row_next = set(tokens(i + 1))
        if "STRIKE" in row_now and len(row_next & fine) >= 3:
            return i + 1

    # Fallback
    return 0

def read_option_chain_file(path: str) -> pd.DataFrame:
    """
    Reads the option chain Excel file with two header rows, creates canonical columns,
    aligns columns and formats expiry date.
    """
    try:
        # Read with two header rows
        df_raw = pd.read_excel(path, header=[0, 1], engine="openpyxl")
        # Produce canonical column names, e.g. ("CALLS","LTP") -> "LTP_CALL"
        def col_name(col):
            sec, sub = str(col[0]).strip().upper(), str(col[1]).strip().upper()
            if sec == "CALLS" and sub not in ("STRIKE", ""):
                return f"{sub}_CALL"
            elif sec == "PUTS" and sub not in ("STRIKE", ""):
                return f"{sub}_PUT"
            elif sub == "STRIKE" or sec == "STRIKE":
                return "STRIKE"
            elif sub == "EXPIRY DATE" or sec == "EXPIRY DATE":
                return "EXPIRY DATE"
            else:
                # For anything else
                return sub or sec
        df_raw.columns = [col_name(x) for x in df_raw.columns.values]
        df = align_columns(df_raw)
        if "EXPIRY DATE" in df.columns:
            df["EXPIRY DATE"] = (
                pd.to_datetime(df["EXPIRY DATE"], errors="coerce")
                .dt.strftime("%d-%b-%Y")
            )
        return df
    except Exception as e:
        app.logger.error(f"read_option_chain_file({os.path.basename(path)}): {e}")
        return pd.DataFrame(columns=OC_COLUMNS)
# ---------------------------------------------------
# Latest-file picker & refresh runner
# ---------------------------------------------------
def latest_option_chain_path(prefix: str) -> Optional[str]:
    """
    Get newest file: uploads/{prefix}_option_chain_*.xlsx
    (uses modification time; independent of exact timestamp format)
    """
    pattern = os.path.join(app.config["UPLOAD_FOLDER"], f"{prefix}_option_chain_*.xlsx")
    candidates = glob.glob(pattern)
    if not candidates:
        return None
    return max(candidates, key=os.path.getmtime)

@app.route("/refresh_option_chain", methods=["POST"])
def refresh_option_chain():
    """
    Run P2.py inside uploads/ so it writes:
      NIFTY_option_chain_<timestamp>.xlsx
      BANKNIFTY_option_chain_<timestamp>.xlsx
    """
    try:
        subprocess.run(
            [sys.executable, "P2.py"],
            cwd=app.config["UPLOAD_FOLDER"],
            check=True,
            timeout=180,
        )
        return jsonify({"ok": True}), 200
    except subprocess.TimeoutExpired:
        return jsonify({"ok": False, "error": "Refresh timed out"}), 504
    except subprocess.CalledProcessError as e:
        return jsonify({"ok": False, "error": f"P2.py failed: {e}"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ---------------------------------------------------
# Routes
# ---------------------------------------------------
@app.route("/")
def index():
    return render_template("index.html")
  
@app.route("/dashboard")
def dashboard():
    return render_template("dashboard.html")

@app.route("/dashboard/pdf")
def dashboard_pdf():
    return render_template("dashboard_pdf.html")

@app.route("/dashboard/volume")
def dashboard_volume():
    return render_template("dashboard_volume.html")

@app.route("/dashboard/rsi_oversold")
def dashboard_rsi_oversold():
    return render_template("dashboard_rsi_oversold.html")
    
@app.route("/volume_contraction_chart")
def volume_contraction_chart():
    """
    Serve a pre-rendered chart if present at
    static/dynamic_plots/volume_contraction.png,
    else return a simple placeholder PNG.
    """
    import io, os
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    dynamic_dir = os.path.join(app.static_folder, "dynamic_plots")
    png_name = "volume_contraction.png"
    png_path = os.path.join(dynamic_dir, png_name)

    if os.path.exists(png_path):
        # serve the saved image
        return send_from_directory(dynamic_dir, png_name)

    # fallback placeholder so the tile isn't broken
    fig, ax = plt.subplots(figsize=(9, 4))
    ax.text(0.5, 0.5, "Volume Contraction preview\n(static image not found)",
            ha="center", va="center")
    ax.set_axis_off()
    buf = io.BytesIO()
    fig.savefig(buf, format="png", bbox_inches="tight", dpi=150)
    plt.close(fig)
    buf.seek(0)
    return Response(buf.getvalue(), mimetype="image/png")

@app.route("/uploads/<filename>")
def uploaded_file(filename):
    return send_from_directory(app.config["UPLOAD_FOLDER"], filename)

@app.route("/get_stock_symbols")
def get_stock_symbols():
    syms = load_stock_symbols()
    return jsonify(syms if syms else [])

@app.route("/get_industries")
def get_industries():
    inds = load_industries()
    return jsonify(inds if inds else [])

@app.route("/get_top_performers")
def get_top_performers():
    industry = request.args.get("industry")
    if not industry:
        return jsonify({"error": "Industry not specified"}), 400
    try:
        df = pd.read_excel(os.path.join(BASE_DIR, "Stocks_top_Perf.xlsx"))
        g = df[df["Industry"] == industry]
        top = g.nlargest(3, "Return over 3years")[["Name", "Return over 3years", "Market Capitalization"]]
        return jsonify(top.to_dict(orient="records"))
    except Exception as e:
        app.logger.warning(f"get_top_performers: {e}")
        return jsonify({"error": "Internal server error"}), 500

@app.route("/get_news_headlines")
def get_news_headlines():
    try:
        return jsonify(fetch_news_from_rss())
    except Exception as e:
        app.logger.warning(f"get_news_headlines: {e}")
        return jsonify({"error": "Failed to fetch news headlines"}), 500

@app.route("/option_chain")
def option_chain():
    def _read_underlying(sym: str):
        try:
            p = Path(app.config["UPLOAD_FOLDER"]) / f"{sym}_underlying.txt"
            return float(p.read_text(encoding="utf-8").strip()) if p.exists() else None
        except Exception:
            return None

    # Auto-refresh only if files/underlyings are missing
    try:
        need = (
            latest_option_chain_path("NIFTY") is None or
            latest_option_chain_path("BANKNIFTY") is None or
            _read_underlying("NIFTY") is None or
            _read_underlying("BANKNIFTY") is None
        )
        if need:
            run_option_chain_export(out_dir=Path(app.config["UPLOAD_FOLDER"]))
            generate_levels_from_latest(out_dir=Path(app.config["UPLOAD_FOLDER"]))
    except Exception as e:
        app.logger.warning(f"auto-refresh failed: {e}")

    underlying_n = _read_underlying("NIFTY")
    underlying_b = _read_underlying("BANKNIFTY")

    # Find newest timestamped files
    nifty_path = latest_option_chain_path("NIFTY")
    banknifty_path = latest_option_chain_path("BANKNIFTY")

    if not nifty_path or not banknifty_path:
        flash("Option chain files not found. Click Refresh to generate.", "warning")
        return render_template(
            "option_chain.html",
            cols=OC_COLUMNS,
            cols_display_n=OC_COLUMNS,
            cols_display_b=OC_COLUMNS,
            nifty_data=[],
            banknifty_data=[],
            nifty_expiry=[], selected_expiry_n=None,
            banknifty_expiry=[], selected_expiry_b=None,
            hi_n={}, hi_b={},               # ← restore to avoid template errors
            underlying_n=underlying_n, underlying_b=underlying_b
        )

    # Read, normalize
    dn = read_option_chain_file(nifty_path)
    db = read_option_chain_file(banknifty_path)

    def pick_nearest(expiries):
        today = datetime.today().date()
        try:
            exp_dates = [datetime.strptime(e, "%d-%b-%Y").date() for e in expiries]
            future = [d for d in exp_dates if d >= today]
            if future:
                return min(future).strftime("%d-%b-%Y")
            else:
                return exp_dates[-1].strftime("%d-%b-%Y") if exp_dates else None
        except Exception:
            return expiries[0] if expiries else None

    def exps(df):
        return sorted(df["EXPIRY DATE"].dropna().unique().tolist()) if "EXPIRY DATE" in df.columns else []
    nifty_expiry = exps(dn)
    banknifty_expiry = exps(db)

    sel_n = request.args.get("expiry_n") or pick_nearest(nifty_expiry)
    sel_b = request.args.get("expiry_b") or pick_nearest(banknifty_expiry)

    def filt(df, e):
        return df[df["EXPIRY DATE"] == e].copy() if e and "EXPIRY DATE" in df.columns else df.copy()
    dn = filt(dn, sel_n)
    db = filt(db, sel_b)

    for d in (dn, db):
        if "EXPIRY DATE" in d.columns:
            d.drop(columns=["EXPIRY DATE"], inplace=True, errors="ignore")

    def scale_mark(df: pd.DataFrame):
        scaled = set()
        # Decrease OI/Chg OI/Vol by 15% and round to 1 dp
        for c in COUNT_COLS:
            if c in df.columns:
                s = pd.to_numeric(df[c], errors="coerce")
                df[c] = (s * 0.85).round(1)
        # Round IV/LTP to 1 dp
        for c in ("IV_CALL","LTP_CALL","IV_PUT","LTP_PUT"):
            if c in df.columns:
                df[c] = pd.to_numeric(df[c], errors="coerce").round(1)
        return df, scaled

    dn, scaledN = scale_mark(dn)
    db, scaledB = scale_mark(db)

    def hi_stats(df: pd.DataFrame):
        stats = {}
        cols = [c for c in df.columns if c.startswith("VOLUME_") or c.startswith("OI_") or ("CHG IN OI" in c)]
        for c in cols:
            col = pd.to_numeric(df[c], errors="coerce")
            mx = col.max(skipna=True)
            if "CHG IN OI" in c:
                neg = col[col < 0].dropna()
                mn = neg.min(skipna=True) if not neg.empty else col[col > 0].min(skipna=True)
            else:
                mn = col[col > 0].min(skipna=True)
            stats[c] = {"max": (None if pd.isna(mx) else float(mx)),
                        "min": (None if pd.isna(mn) else float(mn))}
        return stats

    hi_n = hi_stats(dn)
    hi_b = hi_stats(db)

    cols = OC_COLUMNS[:]
    cols_display_n = [f"{c} (L)" if c in scaledN else c for c in cols]
    cols_display_b = [f"{c} (L)" if c in scaledB else c for c in cols]

    dn = dn.replace({np.nan: None})
    db = db.replace({np.nan: None})

    print("Underlying NIFTY:", underlying_n)
    print("Underlying BANKNIFTY:", underlying_b)

    # 🔑 Always try to create levels (skips if already exists for latest ts)
    try:
        generate_levels_from_latest(out_dir=Path(app.config["UPLOAD_FOLDER"]))
    except Exception as e:
        app.logger.warning(f"levels generation failed: {e}")

    return render_template(
        "option_chain.html",
        cols=cols,
        cols_display_n=cols_display_n,
        cols_display_b=cols_display_b,
        nifty_data=dn.to_dict(orient="records"),
        banknifty_data=db.to_dict(orient="records"),
        nifty_expiry=nifty_expiry, selected_expiry_n=sel_n,
        banknifty_expiry=banknifty_expiry, selected_expiry_b=sel_b,
        hi_n=hi_n, hi_b=hi_b,
        underlying_n=underlying_n, underlying_b=underlying_b
    )
 # --- helper: find latest levels_<ts>.xlsx in uploads ---
def latest_levels_path() -> Optional[str]:
    pattern = os.path.join(app.config["UPLOAD_FOLDER"], "levels_*.xlsx")
    files = glob.glob(pattern)
    if not files:
        return None
    return max(files, key=os.path.getmtime)

@app.route("/key_levels")
def key_levels():
    # Ensure a recent levels file exists
    try:
        generate_levels_from_latest(out_dir=Path(app.config["UPLOAD_FOLDER"]))
    except Exception as e:
        app.logger.warning(f"levels generation failed: {e}")

    lv_path = latest_levels_path()
    if not lv_path or not os.path.exists(lv_path):
        flash("Levels file not found. Use Refresh on Option Chain first.", "warning")
        return redirect(url_for("option_chain"))

    # Read both sheets
    try:
        df_n = pd.read_excel(lv_path, sheet_name="NIFTY", engine="openpyxl")
    except Exception:
        df_n = pd.DataFrame(columns=["EXPIRY DATE","STRIKE","LTP_CALL","LTP_PUT","S1","S2","S3","R1","R2","R3"])
    try:
        df_b = pd.read_excel(lv_path, sheet_name="BANKNIFTY", engine="openpyxl")
    except Exception:
        df_b = pd.DataFrame(columns=["EXPIRY DATE","STRIKE","LTP_CALL","LTP_PUT","S1","S2","S3","R1","R2","R3"])

    # Expiry lists + default selection (nearest)
    def exp_list(df):
        if "EXPIRY DATE" not in df.columns: return []
        xs = pd.to_datetime(df["EXPIRY DATE"], errors="coerce").dt.strftime("%d-%b-%Y")
        return sorted(xs.dropna().unique().tolist())

    def pick_nearest(expiries):
        from datetime import datetime as _dt
        if not expiries: return None
        try:
            ds = [_dt.strptime(e, "%d-%b-%Y").date() for e in expiries]
            today = _dt.today().date()
            fut = [d for d in ds if d >= today]
            return (min(fut) if fut else ds[-1]).strftime("%d-%b-%Y")
        except Exception:
            return expiries[0]

    exps_n = exp_list(df_n)
    exps_b = exp_list(df_b)
    sel_n = request.args.get("expiry_n") or pick_nearest(exps_n)
    sel_b = request.args.get("expiry_b") or pick_nearest(exps_b)

    # Filter by selected expiry
    def filt(df, e):
        if not e or "EXPIRY DATE" not in df.columns: return df.copy()
        t = pd.to_datetime(df["EXPIRY DATE"], errors="coerce").dt.strftime("%d-%b-%Y")
        out = df.copy()
        out = out.loc[t == e]
        return out

    dn = filt(df_n, sel_n)
    db = filt(df_b, sel_b)

    # Clean & order columns for display
    COLS = ["STRIKE","LTP_CALL","LTP_PUT","S1","S2","S3","R1","R2","R3"]
    for d in (dn, db):
        for c in COLS:
            if c not in d.columns:
                d[c] = pd.NA
    dn = dn[COLS].copy()
    db = db[COLS].copy()

    # Underlyings (to mark & center ATM)
    def _read_underlying(sym):
        try:
            p = Path(app.config["UPLOAD_FOLDER"]) / f"{sym}_underlying.txt"
            return float(p.read_text(encoding="utf-8").strip()) if p.exists() else None
        except Exception:
            return None
    underlying_n = _read_underlying("NIFTY")
    underlying_b = _read_underlying("BANKNIFTY")

    # Render
    return render_template(
        "levels.html",
        nifty_data=dn.replace({np.nan: None}).to_dict(orient="records"),
        banknifty_data=db.replace({np.nan: None}).to_dict(orient="records"),
        nifty_expiry=exps_n, selected_expiry_n=sel_n,
        banknifty_expiry=exps_b, selected_expiry_b=sel_b,
        underlying_n=underlying_n, underlying_b=underlying_b
    )
# ---------------------------------------------------
# Main
# ---------------------------------------------------


@app.route("/api/rsi_oversold_csv")
def api_rsi_oversold_csv():
    """
    Serve RSI-oversold rows from local CSV:
    static/dynamic_plots/KK Daily RSI experiment_Oversold.csv
    Returns: { ok: bool, rows: [ {symbol, price, rsi, volume, change} ] }
    """
    import re
    import pandas as pd
    csv_path = os.path.join(
        app.static_folder, "dynamic_plots", "KK Daily RSI experiment_Oversold.csv"
    )
    if not os.path.exists(csv_path):
        return jsonify({"ok": False, "rows": [], "error": "missing_csv"}), 404
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception as e:
        app.logger.warning(f"CSV read failed: {e}")
        return jsonify({"ok": False, "rows": [], "error": "bad_csv"}), 500

    def norm(s):
        s = str(s).strip().lower()
        s = s.replace('%','pct')
        return re.sub(r"[^a-z0-9]+","_", s).strip('_')

    cols = {norm(c): c for c in df.columns}

    sym_col = next((cols[k] for k in cols if k in ("symbol","name","ticker")), None)
    price_col = next((cols[k] for k in cols if k in ("price","ltp","close")), None)
    rsi_col = next((cols[k] for k in cols if k in ("rsi","rsi_14","rsi14")), None)
    vol_col = next((cols[k] for k in cols if k in ("volume","vol")), None)
    chg_col = next((cols[k] for k in cols if k in ("pct_change","change_pct","change","_change","percent_change")), None)

    if not sym_col or not price_col or not rsi_col:
        return jsonify({"ok": False, "rows": [], "error": "missing_columns"}), 422

    rows = []
    for _, r in df.iterrows():
        rows.append({
            "symbol": str(r.get(sym_col, "")),
            "price": None if pd.isna(r.get(price_col)) else r.get(price_col),
            "rsi": None if pd.isna(r.get(rsi_col)) else r.get(rsi_col),
            "volume": None if (vol_col is None or pd.isna(r.get(vol_col))) else r.get(vol_col),
            "change": None if (chg_col is None or pd.isna(r.get(chg_col))) else r.get(chg_col),
        })
    return jsonify({"ok": True, "rows": rows})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    app.run(debug=True, host="0.0.0.0", port=port)
