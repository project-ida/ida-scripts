#!/usr/bin/env python3
"""
Waveform Plotter (ROOT-backed; production + loader + meta & file list)

Query examples:
  /waveforms?table=root_files&start=YYYY-MM-DDTHH:MM:SS&end=...\
      [&channel=7|&device=caen8ch_ch7&max_n=80&granularity=1&yoffset=0
       &filter=cps(>0.18, between 601 and 900)&debug=1]

Routes:
  /waveforms       -> interactive shell (spawns PNG + JSON + META)
  /waveforms.png   -> plot only
  /waveforms.json  -> data + debug (pasteable SQL)
  /waveforms.meta  -> overlapping ROOT files (lightweight)
"""

# Safety caps for the "all pulses with samples" JSON
ALL_JSON_MAX_WAVEFORMS = 2000   # refuse if more than this, unless &force=1

import os, io, re, json
from datetime import datetime, timedelta, timezone
from dateutil import parser as dateparser

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import uproot

from flask import Flask, request, abort, jsonify, Response
from sqlalchemy import create_engine, text

# -------------------- Config --------------------------------------------------

# DB creds from local module (or parent)
import sys
from pathlib import Path
try:
    import psql_credentials
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    import psql_credentials

PGUSER, PGPASSWORD = psql_credentials.PGUSER, psql_credentials.PGPASSWORD
PGHOST, PGPORT     = psql_credentials.PGHOST, psql_credentials.PGPORT
PGDATABASE         = psql_credentials.PGDATABASE
CONNECTION_URI = f"postgresql+psycopg2://{PGUSER}:{PGPASSWORD}@{PGHOST}:{PGPORT}/{PGDATABASE}"
engine = create_engine(CONNECTION_URI, pool_pre_ping=True, future=True)

ROOT_BASE_DIR = "/mnt/gdrive/Computers"  # adjust if needed

DEFAULT_INDEX_TABLE = "root_files"
COL_TIME, COL_COMPUTER, COL_DIR, COL_FILE = "time", "computer", "dir", "file"

TTREE_NAME      = "Data_R"
BR_TIMESTAMP    = "Timestamp"
BR_SAMPLES      = "Samples"
BR_ENERGY       = "Energy"
BR_ENERGY_SHORT = "EnergyShort"
TIMESTAMP_DIVISOR = 1e12  # picoseconds → seconds

DEFAULT_MAX_N, HARD_CAP_N = 80, 500
DEFAULT_GRAN, DEFAULT_YOFFS = 1, 0.0

# HARD LIMIT on how many overlapping ROOT files we'll process
MAX_ROOT_FILES = 10

# -------------------- App ----------------------------------------------------

app = Flask(__name__)
IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

# ------ Helpers ---------------------------------------------------------------

def parse_dt(s: str) -> datetime:
    try:
        return dateparser.isoparse(s)
    except Exception:
        abort(400, f"Invalid timestamp: {s}. Use ISO8601, e.g. 2025-09-08T12:00:00")

def safe_join(base: str, *parts: str) -> str:
    """
    Join path components under a fixed base directory.
    Also normalizes Windows-style separators (`\\`) because some DB rows store
    `dir` with backslashes.
    """
    base_norm = os.path.normpath(str(base))
    cleaned = []
    for p in parts:
        s = str(p).replace("\u00A0", " ").strip()
        s = s.replace("\\", "/").strip("/")
        if not s:
            continue
        if ".." in s.split("/"):
            abort(400, "Invalid path component.")
        cleaned.append(s)

    out = os.path.normpath(os.path.join(base_norm, *cleaned))
    try:
        if os.path.commonpath([base_norm, out]) != base_norm:
            abort(400, "Resolved path escapes base directory.")
    except Exception:
        abort(400, "Failed to resolve safe path.")
    return out

def _to_naive_py_datetime(x):
    """Return a *naive* Python datetime for pandas/np/py datetimes."""
    if isinstance(x, pd.Timestamp):
        if x.tz is not None:
            x = x.tz_localize(None)
        return x.to_pydatetime()
    if isinstance(x, np.datetime64):
        return pd.Timestamp(x).to_pydatetime()
    if isinstance(x, datetime):
        return x.replace(tzinfo=None) if x.tzinfo is not None else x
    return x

def _sql_literal(v):
    """Quote a Python value for Postgres SQL paste."""
    if isinstance(v, pd.Timestamp):
        v = v.to_pydatetime()
    if isinstance(v, datetime):
        if v.tzinfo is not None:
            v = v.astimezone(timezone.utc).replace(tzinfo=None)
        return "timestamp '" + v.strftime("%Y-%m-%d %H:%M:%S") + "'"
    if isinstance(v, str):
        return "'" + v.replace("'", "''") + "'"
    if isinstance(v, (int, float)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"

def _expand_sql(sql, params):
    """Inline named params (:t0, :t1, :pat, …) for copy/paste debugging."""
    q = str(sql)
    for name in sorted(params.keys(), key=len, reverse=True):
        q = re.sub(rf":{name}\b", _sql_literal(params[name]), q)
    return q

# ---- filter parser (cps/cpm) -------------------------------------------------

NUM_RE = r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?"

def _parse_between(text: str):
    m = re.search(r"between\s*(" + NUM_RE + r")\s*and\s*(" + NUM_RE + r")", text, re.I)
    if not m:
        return None
    a, b = float(m.group(1)), float(m.group(2))
    lo, hi = (a, b) if a <= b else (b, a)
    return lo, hi

def _parse_ineq(text: str):
    m = re.search(r"(>=|<=|>|<|=)\s*(" + NUM_RE + r")", text.strip(), re.I)
    if not m:
        return None
    op, val = m.group(1), float(m.group(2))
    if op == ">=": return val, None, "ge"
    if op == ">":  return val, None, "gt"
    if op == "<=": return None, val, "le"
    if op == "<":  return None, val, "lt"
    if op == "=":  return val, val, "eq"
    return None

def parse_filter_expr(filt: str):
    """
    Accepts:  cps(>0.1801, between 601 and 900)
              cps(>0.0, =4095)
              cpm(>=0.2, >300)
              cps(>0.18)   # no energy gate
    Returns dict with:
      kind: "cps"|"cpm"|None
      psd_lo, psd_hi (None if unset)
      e_lo, e_hi     (None if unset)
      raw: original
    """
    out = {"kind": None, "psd_lo": None, "psd_hi": None, "e_lo": None, "e_hi": None, "raw": filt}
    if not filt:
        return out

    s = filt.strip()
    m = re.match(r"^(cps|cpm)\s*\((.*)\)\s*$", s, re.I)
    if not m:
        return out

    out["kind"] = m.group(1).lower()
    inner = m.group(2)

    parts = [p.strip() for p in inner.split(",", 1)]
    psd_part = parts[0] if parts and parts[0] else ""
    e_part   = parts[1] if len(parts) == 2 else ""

    if psd_part:
        b = _parse_between(psd_part)
        if b:
            out["psd_lo"], out["psd_hi"] = b
        else:
            inq = _parse_ineq(psd_part)
            if inq:
                lo, hi, _ = inq
                if lo is not None: out["psd_lo"] = lo
                if hi is not None: out["psd_hi"] = hi

    if e_part:
        b = _parse_between(e_part)
        if b:
            out["e_lo"], out["e_hi"] = b
        else:
            inq = _parse_ineq(e_part)
            if inq:
                lo, hi, _ = inq
                if lo is not None: out["e_lo"] = lo
                if hi is not None: out["e_hi"] = hi

    return out

# ---- request params ----------------------------------------------------------

def get_params():
    table   = request.args.get("table", DEFAULT_INDEX_TABLE).strip()
    start_s = request.args.get("start", "").strip()
    end_s   = request.args.get("end", "").strip()
    channel = request.args.get("channel", "").strip()
    device  = request.args.get("device", "").strip()  # e.g. "caen8ch_ch7"
    max_n   = int(request.args.get("max_n", DEFAULT_MAX_N))
    gran    = int(request.args.get("granularity", DEFAULT_GRAN))
    yoffs   = float(request.args.get("yoffset", DEFAULT_YOFFS))
    filt    = request.args.get("filter", "").strip()
    debug   = request.args.get("debug", "0") in {"1", "true", "yes"}

    if not IDENT_RE.match(table): abort(400, f"Bad table name: {table}")
    if not start_s or not end_s:  abort(400, "Provide start and end query params.")

    start, end = parse_dt(start_s), parse_dt(end_s)
    if end <= start: abort(400, "end must be after start")

    if not channel and device:
        m = re.search(r"_ch(\d+)$", device)
        if m:
            channel = m.group(1)

    max_n = max(1, min(max_n, HARD_CAP_N))
    gran = max(1, gran)

    parsed = parse_filter_expr(filt)

    return {
        "table": table, "start": start, "end": end, "channel": channel,
        "max_n": max_n, "gran": gran, "yoffs": yoffs,
        "filter_raw": filt, "filter": parsed, "device": device or None,
        "debug": debug
    }

# ---- DB + file candidates ----------------------------------------------------

def candidate_files(table: str, start: datetime, end: datetime, channel: str):
    """
    Postgres filename-aware search: extract start/end from filename and find
    rows whose filename interval overlaps [start, end). Keep everything naive/local.
    """
    file_like = "TRUE"
    params = {"t0": start, "t1": end}
    if channel:
        file_like = f"{COL_FILE} LIKE :pat"
        params["pat"] = f"%CH{channel}@%"

    # IMPORTANT:
    #  - no "AT TIME ZONE 'UTC'" here (it caused the +4h shift during EDT)
    #  - cast to ::timestamp to force 'timestamp without time zone' (naive)
    sql = text(f"""
        WITH r AS (
          SELECT {COL_TIME} AS time,
                 {COL_COMPUTER} AS computer,
                 {COL_DIR} AS dir,
                 {COL_FILE} AS file,
                 regexp_match({COL_FILE}, '([0-9]{{8}}_[0-9]{{6}})-([0-9]{{8}}_[0-9]{{6}})') AS m
          FROM {table}
          WHERE {file_like}
        )
        SELECT time, computer, dir, file,
               to_timestamp(m[1], 'YYYYMMDD_HH24MISS')::timestamp AS fname_start,
               to_timestamp(m[2], 'YYYYMMDD_HH24MISS')::timestamp AS fname_end
        FROM r
        WHERE m IS NOT NULL
          -- overlap test with half-open window [t0, t1)
          AND to_timestamp(m[1], 'YYYYMMDD_HH24MISS')::timestamp <  :t1
          AND to_timestamp(m[2], 'YYYYMMDD_HH24MISS')::timestamp >= :t0
        ORDER BY time ASC
        LIMIT 4000
    """)

    with engine.connect() as c:
        df = pd.read_sql(sql, c, params=params)

    # These should already be naive (no tz). Keep this as a guard if DB settings ever change.
    if "fname_start" in df.columns and pd.api.types.is_datetime64tz_dtype(df["fname_start"]):
        df["fname_start"] = df["fname_start"].dt.tz_localize(None)
    if "fname_end" in df.columns and pd.api.types.is_datetime64tz_dtype(df["fname_end"]):
        df["fname_end"] = df["fname_end"].dt.tz_localize(None)

    expanded = _expand_sql(sql, params)
    return df, sql, params, expanded


# ---- ROOT reading / filtering ------------------------------------------------

def extract_window_waveforms(root_path: str,
                             file_start_abs: datetime,
                             start: datetime,
                             end: datetime,
                             max_n: int,
                             gran: int,
                             filt: dict):
    """
    Open ROOT file, compute absolute event times, select entries in (start, end),
    then apply PSD/Energy cuts.

    Returns (series, debug_counts, final_count, details)
      - series: [(iso_time, samples_list), ...]      # used for plotting
      - details: [{"time": iso, "energy": E, "energy_short": Es, "psd": PSD}, ...]
    """
    out = []
    details = []
    dbg = {"scanned": 0, "time_match": 0, "psd_keep": 0, "energy_keep": 0, "final_keep": 0}

    with uproot.open(root_path) as f:
        if TTREE_NAME not in f:
            return out, dbg, 0, details
        t = f[TTREE_NAME]

        ts = t[BR_TIMESTAMP].array(library="np")
        if ts is None or len(ts) == 0:
            return out, dbg, 0, details

        rel_s = (ts - ts[0]) / TIMESTAMP_DIVISOR
        abs_times = np.array([file_start_abs + timedelta(seconds=float(s)) for s in rel_s])

        # OPEN interval to match DB screenshots: (start, end)
        mask_time = (abs_times > start) & (abs_times < end)
        dbg["scanned"] = int(len(ts))
        dbg["time_match"] = int(mask_time.sum())
        if dbg["time_match"] == 0:
            return out, dbg, 0, details

        mask = mask_time
        have_energy = (BR_ENERGY in t.keys()) and (BR_ENERGY_SHORT in t.keys())
        e = es = None
        if have_energy:
            # Always load energy branches so they can be displayed in the pulse list,
            # even when no filter is provided.
            e  = t[BR_ENERGY].array(library="np")
            es = t[BR_ENERGY_SHORT].array(library="np")

        if have_energy and (filt.get("psd_lo") is not None or filt.get("psd_hi") is not None):
            with np.errstate(divide="ignore", invalid="ignore"):
                psd = 1.0 - (es / e)
            # Drop invalid PSDs; if you prefer to treat them as 0, replace next line with:
            # psd = np.where(np.isfinite(psd), psd, 0.0)
            m = np.isfinite(psd)
            if filt.get("psd_lo") is not None: m &= (psd >= float(filt["psd_lo"]))
            if filt.get("psd_hi") is not None: m &= (psd <= float(filt["psd_hi"]))
            mask &= m
            dbg["psd_keep"] = int(np.logical_and(mask_time, m).sum())
        else:
            dbg["psd_keep"] = dbg["time_match"]

        if have_energy and (filt.get("e_lo") is not None or filt.get("e_hi") is not None):
            m = np.ones_like(e, dtype=bool)
            if filt.get("e_lo") is not None: m &= (e >= float(filt["e_lo"]))
            if filt.get("e_hi") is not None: m &= (e <= float(filt["e_hi"]))
            mask &= m
            dbg["energy_keep"] = int(np.logical_and(mask_time, m).sum())
        else:
            dbg["energy_keep"] = dbg["psd_keep"]

        if not mask.any():
            return out, dbg, 0, details

        dbg["final_keep"] = int(mask.sum())
        total_matching = dbg["final_keep"]

        samples = t[BR_SAMPLES].array(library="np")  # jagged (object)
        idx = np.flatnonzero(mask)  # we’ll cap at caller when appending

        for i in idx:
            # waveform
            y = samples[i]
            try:
                y = np.asarray(y)
            except Exception:
                y = np.array(list(y))
            y = y[::gran] if gran > 1 else y.copy()

            # meta for list
            iso_t = abs_times[i].isoformat()
            Ei = Esi = P = None
            if have_energy:
                try:
                    Ei  = float(e[i])
                    Esi = float(es[i])
                    with np.errstate(divide="ignore", invalid="ignore"):
                        P = float(1.0 - (Esi / Ei)) if Ei else None
                    if not np.isfinite(P):
                        P = None
                except Exception:
                    pass

            out.append((iso_t, y.tolist()))
            details.append({
                "time": iso_t,
                "energy": None if Ei is None or not np.isfinite(Ei) else Ei,
                "energy_short": None if Esi is None or not np.isfinite(Esi) else Esi,
                "psd": P,
            })

    return out, dbg, total_matching, details

def collect_series(params):
    """
    Heavy path: returns (series_for_plot, files_used, debug, total_matching_all_files, pulses_plotted)
      - series_for_plot is capped at max_n
      - pulses_plotted corresponds 1:1 with the plotted series (same cap and order)
      - total_matching_all_files counts matches across all overlapping files (uncapped)
    """
    table = params["table"]; start = params["start"]; end = params["end"]
    channel = params["channel"]; max_n = params["max_n"]; gran = params["gran"]
    filt = params["filter"]; debug = params["debug"]

    idx_df, sql, sql_params, sql_expanded = candidate_files(table, start, end, channel)
    if debug:
        print("Expanded SQL:\n", sql_expanded)

    # Hard cap on overlapping files
    overlap_n = int(len(idx_df))
    if overlap_n > MAX_ROOT_FILES:
        msg = (f"Too many ROOT files match the selected window "
               f"({overlap_n} > {MAX_ROOT_FILES}). Please select a narrower time span.")
        abort(400, msg)

    files_used, series = [], []
    debug_per_file = []
    total_matching = 0
    pulses_plotted = []

    for _, row in idx_df.iterrows():
        root_path = safe_join(ROOT_BASE_DIR, row[COL_COMPUTER], row[COL_DIR], row[COL_FILE])
        if not os.path.exists(root_path):
            continue

        fstart = _to_naive_py_datetime(row["fname_start"])
        fend   = _to_naive_py_datetime(row["fname_end"])

        # safety, though SQL already overlaps
        if (fend <= start) or (fstart >= end):
            continue

        try:
            chunk, dbg, nmatch, dets = extract_window_waveforms(
                root_path=root_path,
                file_start_abs=fstart,
                start=start, end=end,
                max_n=max_n, gran=gran,
                filt=filt
            )
        except Exception:
            continue

        total_matching += nmatch  # count all, even if we cap plotting

        # Append up to remaining slots for plotting
        if len(series) < max_n and chunk:
            slots = max_n - len(series)
            used_chunk = chunk[:slots]
            used_dets  = dets[:slots]
            series.extend(used_chunk)
            pulses_plotted.extend(used_dets)
            files_used.append({
                "path": root_path,
                "file_start": fstart.isoformat(),
                "file_end": fend.isoformat(),
                "count": len(used_chunk),  # how many from this file were actually plotted
            })
        elif chunk:
            # nothing appended, but still report file window
            files_used.append({
                "path": root_path,
                "file_start": fstart.isoformat(),
                "file_end": fend.isoformat(),
                "count": 0,
            })

        if debug:
            debug_per_file.append({
                "path": root_path,
                "file_start": fstart.isoformat(),
                "file_end": fend.isoformat(),
                **dbg
            })

    # sort plotted data by time (string ISO sorts lexicographically OK)
    series = sorted(series, key=lambda x: x[0])[:max_n]
    pulses_plotted.sort(key=lambda d: d["time"])

    dbg = {
        "sql": str(sql),
        "sql_params": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in sql_params.items()},
        "expanded_sql": sql_expanded,
        "candidate_count": int(len(idx_df)),
        "per_file": debug_per_file
    }
    return series, files_used, dbg, total_matching, pulses_plotted

def list_files_in_window(params):
    """Lightweight path: list overlapping files (no samples loaded)."""
    table = params["table"]; start = params["start"]; end = params["end"]
    channel = params["channel"]

    idx_df, sql, sql_params, sql_expanded = candidate_files(table, start, end, channel)
    overlap_n = int(len(idx_df))
    if overlap_n > MAX_ROOT_FILES:
        return None, {
            "sql": str(sql),
            "sql_params": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in sql_params.items()},
            "expanded_sql": sql_expanded,
            "candidate_count": overlap_n
        }, f"Too many ROOT files match the selected window ({overlap_n} > {MAX_ROOT_FILES}). Please select a narrower time span."

    files = []
    for _, row in idx_df.iterrows():
        root_path = safe_join(ROOT_BASE_DIR, row[COL_COMPUTER], row[COL_DIR], row[COL_FILE])
        if not os.path.exists(root_path):
            continue
        fstart = _to_naive_py_datetime(row["fname_start"])
        fend   = _to_naive_py_datetime(row["fname_end"])
        if (fend <= start) or (fstart >= end):
            continue
        files.append({
            "path": root_path,
            "file_start": fstart.isoformat(),
            "file_end": fend.isoformat(),
            "num_entries": None,
        })
    dbg = {
        "sql": str(sql),
        "sql_params": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in sql_params.items()},
        "expanded_sql": sql_expanded,
        "candidate_count": overlap_n
    }
    return files, dbg, None

def plot_waveforms(series, title, yoffset):
    """
    Plot waveforms; legend entries show pulse time as HH:MM:SS.ffffff so they
    can be matched to the table below precisely (microsecond resolution).
    """
    if not series:
        fig, ax = plt.subplots(figsize=(6, 2), dpi=130)
        ax.text(0.5, 0.5, "No waveforms in this window", ha="center", va="center")
        ax.axis("off")
        buf = io.BytesIO(); fig.savefig(buf, format="png", bbox_inches="tight"); plt.close(fig)
        return buf.getvalue()

    fig, ax = plt.subplots(figsize=(11.5, 6.5), dpi=130)
    ax.set_title(title)
    ax.set_xlabel("Sample index")
    ax.set_ylabel("Amplitude")
    ax.grid(True, alpha=0.25)

    handles, labels = [], []

    for i, (t_iso, y) in enumerate(series):
        # Legend label as HH:MM:SS.ffffff
        try:
            dt = datetime.fromisoformat(t_iso)
        except Exception:
            dt = dateparser.isoparse(t_iso)
        label = dt.strftime("%H:%M:%S.%f")

        yy = y if yoffset == 0 else [v + i * yoffset for v in y]
        (line,) = ax.plot(range(len(yy)), yy, linewidth=0.7)
        handles.append(line); labels.append(label)

    fig.subplots_adjust(bottom=0.28)  # make room at the bottom

    ax.legend(
        handles, labels,
        title="Time (hh:mm:ss.ffffff)",
        loc="upper center",
        bbox_to_anchor=(0.5, -0.14),  # below axes
        ncol=min(6, max(1, int(len(labels) / 10))),  # spread across columns
        fontsize=8,
        framealpha=0.4,
    )

    ax.text(0.01, 0.99, f"{len(series)} waveform(s)",
            transform=ax.transAxes, va="top", ha="left")

    buf = io.BytesIO()
    fig.tight_layout()
    fig.savefig(buf, format="png")
    plt.close(fig)
    return buf.getvalue()

# ---- NEW: collect *all* matching pulses (no waveforms) ----------------------

def extract_window_fullsamples(root_path: str,
                               file_start_abs: datetime,
                               start: datetime,
                               end: datetime,
                               gran: int,
                               filt: dict):
    """
    Like extract_window_pulsemeta but also returns the waveform samples for *each* pulse.
    Returns (records, debug_counts, final_count)
    """
    out = []
    dbg = {"scanned": 0, "time_match": 0, "psd_keep": 0, "energy_keep": 0, "final_keep": 0}

    with uproot.open(root_path) as f:
        if TTREE_NAME not in f:
            return out, dbg, 0
        t = f[TTREE_NAME]

        ts = t[BR_TIMESTAMP].array(library="np")
        if ts is None or len(ts) == 0:
            return out, dbg, 0

        rel_s = (ts - ts[0]) / TIMESTAMP_DIVISOR
        abs_times = np.array([file_start_abs + timedelta(seconds=float(s)) for s in rel_s])

        # time window (OPEN interval to match DB screenshots)
        mask_time = (abs_times > start) & (abs_times < end)
        dbg["scanned"] = int(len(ts))
        dbg["time_match"] = int(mask_time.sum())
        if dbg["time_match"] == 0:
            return out, dbg, 0

        have_energy = (BR_ENERGY in t.keys()) and (BR_ENERGY_SHORT in t.keys())
        e = es = None
        if have_energy:
            # Always load energy branches so they can be returned in JSON even when
            # no filter is provided.
            e  = t[BR_ENERGY].array(library="np")
            es = t[BR_ENERGY_SHORT].array(library="np")
        mask = mask_time

        if have_energy and (filt.get("psd_lo") is not None or filt.get("psd_hi") is not None):
            with np.errstate(divide="ignore", invalid="ignore"):
                psd = 1.0 - (es / e)
            m = np.isfinite(psd)
            if filt.get("psd_lo") is not None: m &= (psd >= float(filt["psd_lo"]))
            if filt.get("psd_hi") is not None: m &= (psd <= float(filt["psd_hi"]))
            mask &= m
            dbg["psd_keep"] = int(np.logical_and(mask_time, m).sum())
        else:
            dbg["psd_keep"] = dbg["time_match"]

        if have_energy and (filt.get("e_lo") is not None or filt.get("e_hi") is not None):
            m = np.ones_like(e, dtype=bool)
            if filt.get("e_lo") is not None: m &= (e >= float(filt["e_lo"]))
            if filt.get("e_hi") is not None: m &= (e <= float(filt["e_hi"]))
            mask &= m
            dbg["energy_keep"] = int(np.logical_and(mask_time, m).sum())
        else:
            dbg["energy_keep"] = dbg["psd_keep"]

        if not mask.any():
            return out, dbg, 0

        idx = np.flatnonzero(mask)
        dbg["final_keep"] = int(idx.size)

        samples = t[BR_SAMPLES].array(library="np")  # jagged/object

        for i in idx:
            y = samples[i]
            try:
                y = np.asarray(y)
            except Exception:
                y = np.array(list(y))
            if gran > 1:
                y = y[::gran]
            y = y.tolist()

            Ei = Esi = P = None
            if have_energy:
                try:
                    Ei  = float(e[i])
                    Esi = float(es[i])
                    with np.errstate(divide="ignore", invalid="ignore"):
                        P = float(1.0 - (Esi / Ei)) if Ei else None
                    if not np.isfinite(P): P = None
                    if not np.isfinite(Ei): Ei = None
                    if not np.isfinite(Esi): Esi = None
                except Exception:
                    Ei = Esi = P = None

            out.append({
                "time": abs_times[i].isoformat(),
                "samples": y,
                "energy": Ei,
                "energy_short": Esi,
                "psd": P
            })

    return out, dbg, dbg["final_keep"]

def collect_all_pulses_with_samples(params):
    """
    Returns (files_used, dbg, pulses_all, total_matching_all_files).
    Includes waveform samples for every matching pulse (downsampled by gran).
    """
    table = params["table"]; start = params["start"]; end = params["end"]
    channel = params["channel"]; filt = params["filter"]; debug = params["debug"]
    gran = params["gran"]

    idx_df, sql, sql_params, sql_expanded = candidate_files(table, start, end, channel)

    overlap_n = int(len(idx_df))
    if overlap_n > MAX_ROOT_FILES:
        abort(400, f"Too many ROOT files match the selected window ({overlap_n} > {MAX_ROOT_FILES}). Please select a narrower time span.")

    files_used, pulses_all = [], []
    debug_per_file = []
    total_matching = 0

    for _, row in idx_df.iterrows():
        root_path = safe_join(ROOT_BASE_DIR, row[COL_COMPUTER], row[COL_DIR], row[COL_FILE])
        if not os.path.exists(root_path):
            continue

        fstart = _to_naive_py_datetime(row["fname_start"])
        fend   = _to_naive_py_datetime(row["fname_end"])
        if (fend <= start) or (fstart >= end):
            continue

        try:
            recs, dbg, nmatch = extract_window_fullsamples(
                root_path=root_path,
                file_start_abs=fstart,
                start=start, end=end,
                gran=gran,
                filt=filt
            )
        except Exception:
            continue

        total_matching += nmatch
        pulses_all.extend(recs)
        files_used.append({
            "path": root_path,
            "file_start": fstart.isoformat(),
            "file_end": fend.isoformat(),
            "count": int(nmatch),
        })

        if debug:
            debug_per_file.append({
                "path": root_path,
                "file_start": fstart.isoformat(),
                "file_end": fend.isoformat(),
                **dbg
            })

    # chronological
    pulses_all.sort(key=lambda d: d["time"])
    dbg = {
        "sql": str(sql),
        "sql_params": {k: (v.isoformat() if hasattr(v, "isoformat") else v) for k, v in sql_params.items()},
        "expanded_sql": sql_expanded,
        "candidate_count": int(len(idx_df)),
        "per_file": debug_per_file
    }
    return files_used, dbg, pulses_all, int(total_matching)

# -------------------- Routes --------------------------------------------------

@app.route("/waveforms")
def waveforms_shell():
    """Fast shell with spinner, meta, and file list (meta loads quickly)."""
    p = get_params()
    qs = request.query_string.decode("utf-8")
    json_url = f"/waveforms.json?{qs}"
    png_url  = f"/waveforms.png?{qs}"
    meta_url = f"/waveforms.meta?{qs}"
    filt = p["filter"]

    # human-readable filter bits
    psd_txt = None
    if filt.get("psd_lo") is not None or filt.get("psd_hi") is not None:
        a = []
        if filt.get("psd_lo") is not None: a.append(f"min={filt['psd_lo']}")
        if filt.get("psd_hi") is not None: a.append(f"max={filt['psd_hi']}")
        psd_txt = ", ".join(a)

    energy_txt = None
    if filt.get("e_lo") is not None or filt.get("e_hi") is not None:
        b = []
        if filt.get("e_lo") is not None: b.append(f"min={int(filt['e_lo'])}")
        if filt.get("e_hi") is not None: b.append(f"max={int(filt['e_hi'])}")
        energy_txt = ", ".join(b)

    device_line = f"<div><b>Device:</b> {p['device']}</div>" if p.get("device") else ""
    channel_line = f"<div><b>Channel:</b> {p['channel']}</div>" if p.get("channel") else ""

    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Waveforms</title>
<style>
  body{{font-family:system-ui,Segoe UI,Roboto,Helvetica,Arial,sans-serif;margin:24px}}
  .meta{{color:#444;margin:12px 0}}
  .muted{{color:#666;font-size:0.9rem}}
  #spinner{{width:46px;height:46px;border:5px solid #ddd;border-top-color:#3a68ff;border-radius:50%;
           animation:spin 1s linear infinite;margin:24px auto}}
  @keyframes spin{{to{{transform:rotate(360deg)}}}}
  #plot{{display:none;max-width:100%;height:auto;border:1px solid #ddd}}
  ul{{padding-left:1.2rem}}
  details pre{{background:#f6f8fa;padding:8px;border-radius:6px;overflow:auto}}
  .error{{color:#b00020}}
  table.pulselist td, table.pulselist th {{ border-bottom:1px solid #eee; }}
</style>
</head><body>
  <h1>Waveforms</h1>

  <div class="meta">
    <div><b>Index table:</b> {p['table']}</div>
    <div><b>Window:</b> {p['start'].isoformat()} → {p['end'].isoformat()}</div>
    {device_line}
    {channel_line}
    <div><b>Filter:</b> {p['filter_raw'] or "—"}</div>
    {"<div><b>PSD:</b> " + psd_txt + "</div>" if psd_txt else ""}
    {"<div><b>Energy:</b> " + energy_txt + "</div>" if energy_txt else ""}
    <div><b>Granularity:</b> {p['gran']}  &nbsp; <b>Y offset:</b> {p['yoffs']} (can be controlled via the URL, e.g. add "&yoffset=100")</div>
    <div>Add "&debug=1" to the URL to see DB queries</div>
  </div>

  <h3>Files in range</h3>
  
  <div class="muted" id="files-note">Finding overlapping ROOT files…</div>
  <ul id="filelist"></ul>

  <div><b>Returned:</b> <span id="m-returned">loading…</span></div>

  <div id="spinner" aria-label="loading"></div>
  <img id="plot" alt="waveform-plot">
  <div id="pulse-list" class="muted" style="margin-top:10px; font-size:12px;"></div>

  <div style="margin-top:8px;">
    <a id="json_plot" href="{json_url}">Download JSON (plotted selection)</a>
    &nbsp;|&nbsp;
    <a id="json_all"  href="/waveforms.all.json?{qs}">Download JSON (all matching pulses)</a>
  </div>

  {"<details style='margin-top:14px;'><summary>Debug</summary><div id='dbg'></div></details>" if p["debug"] else ""}

<script>
  const jsonUrl = "{json_url}";
  const pngUrl  = "{png_url}";
  const metaUrl = "{meta_url}";
  const debugOn = {"true" if p["debug"] else "false"};

  // list files fast (and show 'too many files' error, if any)
  fetch(metaUrl, {{ cache: "no-store" }})
    .then(async r => {{
      let data;
      try {{ data = await r.json(); }} catch(e) {{ data = null; }}
      const list = document.getElementById('filelist');
      const note = document.getElementById('files-note');
      if (!r.ok) {{
        note.className = 'error';
        note.textContent = (data && data.error) ? data.error : 'Failed to fetch file list.';
        if (debugOn && data && data.debug) {{
          const dbg = document.getElementById('dbg');
          dbg.innerHTML = "<b>SQL (pasteable):</b><pre>" + (data.debug.expanded_sql || data.debug.sql) + "</pre>" +
                          "<b>SQL (with params):</b><pre>" + data.debug.sql + "</pre>" +
                          "<b>params:</b><pre>" + JSON.stringify(data.debug.sql_params, null, 2) + "</pre>" +
                          "<b>candidate rows:</b> " + data.debug.candidate_count;
        }}
        return;
      }}
      note.textContent = data.files.length ? "" : "No overlapping ROOT files found.";
      list.innerHTML = "";
      data.files.forEach(f => {{
        const li = document.createElement('li');
        const path = document.createElement('code'); path.textContent = f.path;
        const span = document.createElement('span'); span.className = 'muted';
        if (f.file_start && f.file_end) span.textContent = "  (" + f.file_start + " → " + f.file_end + ")";
        li.appendChild(path); li.appendChild(span);
        list.appendChild(li);
      }});
      if (debugOn && data.debug) {{
        const dbg = document.getElementById('dbg');
        dbg.innerHTML = "<b>SQL (pasteable):</b><pre>" + (data.debug.expanded_sql || data.debug.sql) + "</pre>" +
                        "<b>SQL (with params):</b><pre>" + data.debug.sql + "</pre>" +
                        "<b>params:</b><pre>" + JSON.stringify(data.debug.sql_params, null, 2) + "</pre>" +
                        "<b>candidate rows:</b> " + data.debug.candidate_count;
      }}
    }});

  // start PNG load
  const img = document.getElementById('plot');
  const spin = document.getElementById('spinner');
  img.onload = () => {{ spin.style.display='none'; img.style.display='block'; }};
  img.onerror = () => {{
    spin.style.display='none';
    const div = document.createElement('div');
    div.className = 'muted';
    div.innerText = 'Failed to load plot.';
    img.replaceWith(div);
  }};
  img.src = pngUrl;

  // fetch JSON to fill the returned line, per-file debug, and the pulse list
  fetch(jsonUrl, {{ cache: "no-store" }})
    .then(async r => {{
      let d;
      try {{ d = await r.json(); }} catch(e) {{ d = null; }}
      const mReturned = document.getElementById('m-returned');
      if (!r.ok) {{
        mReturned.className = 'error';
        mReturned.textContent = (d && d.error) ? d.error : 'Failed to load waveforms.';
        if (debugOn && d && d.debug) {{
          const dbg = document.getElementById('dbg');
          dbg.innerHTML += "<hr><b>SQL (pasteable):</b><pre>" + (d.debug.expanded_sql || d.debug.sql) + "</pre>";
        }}
        return;
      }}
      mReturned.textContent = d.count + " of " + d.total_matching +
        " waveform(s) (max_n=" + d.max_n + ", granularity=" + d.granularity + ", yoffset=" + d.yoffset + ")";
      document.getElementById('json_plot').href = "{json_url}";
      document.getElementById('json_all').href  = "/waveforms.all.json?{qs}";

      // ---- Pulse list (plotted only), sorted by timestamp ----
      const host = document.getElementById('pulse-list');
      const pulses = Array.isArray(d.pulses) ? d.pulses : [];
      if (!pulses.length) {{
        host.textContent = "No pulse metadata to list.";
      }} else {{
        const tbl = document.createElement('table');
        tbl.className = 'pulselist';
        tbl.style.borderCollapse = 'collapse';
        const thead = document.createElement('thead');
        thead.innerHTML = '<tr><th style="text-align:left;padding:2px 6px;">Time</th>' +
                          '<th style="text-align:right;padding:2px 6px;">Energy</th>' +
                          '<th style="text-align:right;padding:2px 6px;">EnergyShort</th>' +
                          '<th style="text-align:right;padding:2px 6px;">PSD</th></tr>';
        tbl.appendChild(thead);
        const tb = document.createElement('tbody');
        pulses.forEach(p => {{
          const tr = document.createElement('tr');
          const td = (txt, right=false) => {{
            const e = document.createElement('td');
            e.style.padding = '2px 6px';
            e.style.textAlign = right ? 'right' : 'left';
            e.textContent = txt;
            return e;
          }};
          tr.appendChild(td(p.time || '—'));
          const E  = (p.energy != null && isFinite(p.energy)) ? Math.round(p.energy).toString() : '—';
          const Es = (p.energy_short != null && isFinite(p.energy_short)) ? Math.round(p.energy_short).toString() : '—';
          const P  = (p.psd != null && isFinite(p.psd)) ? p.psd.toFixed(4) : '—';
          tr.appendChild(td(E,  true));
          tr.appendChild(td(Es, true));
          tr.appendChild(td(P,  true));
          tb.appendChild(tr);
        }});
        tbl.appendChild(tb);
        host.innerHTML = '<b>Pulses (plotted):</b>';
        host.appendChild(tbl);
      }}

      if (debugOn && d.debug) {{
        const dbg = document.getElementById('dbg');
        dbg.innerHTML += "<hr><b>SQL (pasteable):</b><pre>" + (d.debug.expanded_sql || d.debug.sql) + "</pre>";
        dbg.innerHTML += "<b>Per-file:</b><pre>" + JSON.stringify(d.debug.per_file, null, 2) + "</pre>";
      }}
    }});
</script>
</body></html>"""

@app.route("/waveforms.png")
def waveforms_png():
    try:
        p = get_params()
        series, _files, _dbg, _total, _pulses = collect_series(p)
    except Exception as e:
        return Response(str(e), status=400, mimetype="text/plain")
    png = plot_waveforms(series, f"Waveforms ({p['start'].isoformat()} → {p['end'].isoformat()})", p["yoffs"])
    return Response(png, mimetype="image/png",
                    headers={"Cache-Control": "no-store"})

@app.route("/waveforms.json")
def waveforms_json():
    try:
        p = get_params()
        series, files_used, dbg, total_matching, pulses = collect_series(p)
    except Exception as e:
        return jsonify({"error": str(e)}), 400, {"Cache-Control": "no-store"}
    return jsonify({
        "table": p["table"],
        "start": p["start"].isoformat(),
        "end": p["end"].isoformat(),
        "channel": p["channel"] or None,
        "device": p["device"] or None,
        "filter_raw": p["filter_raw"],
        "filter_parsed": p["filter"],
        "count": len(series),
        "total_matching": int(total_matching),
        "max_n": p["max_n"],
        "granularity": p["gran"],
        "yoffset": p["yoffs"],
        "files": files_used,
        "data": [{"time": t, "samples": y} for (t, y) in series],
        "pulses": pulses,  # plotted pulses (ordered by time)
        "debug": dbg if p["debug"] else None
    }), 200, {"Cache-Control": "no-store"}

@app.route("/waveforms.all.json")
def waveforms_all_json():
    try:
        p = get_params()
        force = request.args.get("force", "").lower() in {"1","true","yes"}
        files_used, dbg, pulses_all, total_matching = collect_all_pulses_with_samples(p)

        # Safety guard unless force=1
        if not force and len(pulses_all) > ALL_JSON_MAX_WAVEFORMS:
            msg = (f"Too many matching pulses for a single JSON payload "
                   f"({len(pulses_all)} > {ALL_JSON_MAX_WAVEFORMS}). "
                   f"Please narrow the time window or re-run with &force=1 (careful: very large JSON).")
            return jsonify({"error": msg, "returned_pulses": len(pulses_all),
                            "total_matching": total_matching}), 413, {"Cache-Control": "no-store"}

    except Exception as e:
        return jsonify({"error": str(e)}), 400, {"Cache-Control": "no-store"}

    return jsonify({
        "table": p["table"],
        "start": p["start"].isoformat(),
        "end": p["end"].isoformat(),
        "channel": p["channel"] or None,
        "device": p["device"] or None,
        "filter_raw": p["filter_raw"],
        "filter_parsed": p["filter"],
        "granularity": p["gran"],
        "returned_pulses": len(pulses_all),
        "total_matching": int(total_matching),
        "files": files_used,
        "pulses": pulses_all,     # EVERY matching pulse, with samples 
        "debug": dbg if p["debug"] else None
    }), 200, {"Cache-Control": "no-store"}

@app.route("/waveforms.meta")
def waveforms_meta():
    p = get_params()
    files, dbg, err = list_files_in_window(p)
    if err:
        return jsonify({"error": err, "debug": dbg}), 400, {"Cache-Control": "no-store"}
    return jsonify({
        "table": p["table"],
        "start": p["start"].isoformat(),
        "end": p["end"].isoformat(),
        "channel": p["channel"] or None,
        "device": p["device"] or None,
        "filter_raw": p["filter_raw"],
        "filter_parsed": p["filter"],
        "files": files,
        "debug": dbg if p["debug"] else None
    }), 200, {"Cache-Control": "no-store"}

# Local dev only (use gunicorn in prod)
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=False)
