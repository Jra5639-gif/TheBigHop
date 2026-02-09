#!/usr/bin/env python3
import os
import re
import time
import json
import sqlite3
import math
from datetime import datetime, timezone
from typing import Optional, Dict, Any, Tuple

import requests
from flask import Flask, request, jsonify, send_from_directory

# ----------------------------
# Config
# ----------------------------
APP_DIR = os.path.dirname(os.path.abspath(__file__))
BASE_DIR = os.path.dirname(APP_DIR)
SITE_DIR = os.path.join(BASE_DIR, "site")
DATA_DIR = os.path.join(SITE_DIR, "data")
DB_PATH = os.path.join(APP_DIR, "travel.db")

BTC_ADDRESS = os.environ.get("TM_BTC_ADDRESS", "bc1qexampleaddressxxxxxxxxxxxxxxxxxxxxxx")
ORIGIN_LABEL = os.environ.get("TM_ORIGIN_LABEL", "Vancouver Island, BC, Canada")

BLOCKSTREAM_TX_URL = "https://blockstream.info/api/tx/{txid}"
BLOCKSTREAM_ADDR_URL = "https://blockstream.info/api/address/{addr}"

NOMINATIM_URL = "https://nominatim.openstreetmap.org/search"

ALIAS_MAX = 30
CITY_MAX = 60
COUNTRY_MAX = 60

# Basic anti-abuse
RATE_LIMIT_WINDOW_S = 60
RATE_LIMIT_MAX = 12

UA = "TravelingMessage/1.0 (contact: none)"

# ----------------------------
# Helpers
# ----------------------------
def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")

def sanitize_text(s: str, max_len: int) -> str:
    s = (s or "").strip()
    s = re.sub(r"\s+", " ", s)
    s = s[:max_len]
    return s

def valid_txid(txid: str) -> bool:
    return bool(re.fullmatch(r"[0-9a-fA-F]{64}", txid or ""))

def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    cur.execute("""
      CREATE TABLE IF NOT EXISTS entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        txid TEXT UNIQUE NOT NULL,
        alias TEXT,
        city TEXT NOT NULL,
        country TEXT NOT NULL,
        lat REAL NOT NULL,
        lng REAL NOT NULL,
        amount_btc REAL NOT NULL,
        iso_date TEXT NOT NULL
      );
    """)
    cur.execute("""
      CREATE TABLE IF NOT EXISTS meta (
        k TEXT PRIMARY KEY,
        v TEXT NOT NULL
      );
    """)
    cur.execute("INSERT OR IGNORE INTO meta (k, v) VALUES (?, ?)", ("btc_address", BTC_ADDRESS))
    cur.execute("INSERT OR IGNORE INTO meta (k, v) VALUES (?, ?)", ("origin_label", ORIGIN_LABEL))
    con.commit()
    con.close()

def db():
    con = sqlite3.connect(DB_PATH)
    con.row_factory = sqlite3.Row
    return con

def geocode_city_country(city: str, country: str) -> Optional[Tuple[float, float]]:
    # City-level geocode via Nominatim. Cache by writing to meta JSON file.
    q = f"{city}, {country}"
    params = {"format": "json", "q": q, "limit": 1}
    r = requests.get(NOMINATIM_URL, params=params, headers={"User-Agent": UA}, timeout=10)
    if r.status_code != 200:
        return None
    data = r.json()
    if not data:
        return None
    lat = float(data[0]["lat"])
    lon = float(data[0]["lon"])
    return (lat, lon)

def tx_amount_to_our_address_btc(txid: str, addr: str) -> Optional[float]:
    # Verify TX exists and includes an output paying to our address.
    r = requests.get(BLOCKSTREAM_TX_URL.format(txid=txid), headers={"User-Agent": UA}, timeout=10)
    if r.status_code != 200:
        return None
    tx = r.json()
    vouts = tx.get("vout", [])
    sats = 0
    for o in vouts:
        spk = o.get("scriptpubkey_address")
        if spk == addr:
            sats += int(o.get("value", 0))
    if sats <= 0:
        return None
    return sats / 1e8

def address_balance_btc(addr: str) -> Optional[float]:
    r = requests.get(BLOCKSTREAM_ADDR_URL.format(addr=addr), headers={"User-Agent": UA}, timeout=10)
    if r.status_code != 200:
        return None
    j = r.json()
    chain = j.get("chain_stats", {})
    mem = j.get("mempool_stats", {})
    funded = int(chain.get("funded_txo_sum", 0)) + int(mem.get("funded_txo_sum", 0))
    spent = int(chain.get("spent_txo_sum", 0)) + int(mem.get("spent_txo_sum", 0))
    return (funded - spent) / 1e8

def export_log_json():
    con = db()
    cur = con.cursor()
    rows = cur.execute("SELECT * FROM entries ORDER BY id ASC").fetchall()
    con.close()
    entries = []
    for r in rows:
        entries.append({
            "txid": r["txid"],
            "alias": r["alias"] or "",
            "city": r["city"],
            "country": r["country"],
            "lat": float(r["lat"]),
            "lng": float(r["lng"]),
            "amount_btc": float(r["amount_btc"]),
            "iso_date": r["iso_date"],
        })
    out = {
        "project": {
            "origin_label": ORIGIN_LABEL,
            "btc_address": BTC_ADDRESS,
            "exported_iso": utc_now_iso(),
        },
        "entries": entries
    }
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(os.path.join(DATA_DIR, "log.json"), "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2)

# ----------------------------
# Rate limiter
# ----------------------------
_ip_hits: Dict[str, list] = {}

def rate_limited(ip: str) -> bool:
    now = time.time()
    hits = _ip_hits.get(ip, [])
    hits = [t for t in hits if now - t < RATE_LIMIT_WINDOW_S]
    hits.append(now)
    _ip_hits[ip] = hits
    return len(hits) > RATE_LIMIT_MAX

# ----------------------------
# Flask app
# ----------------------------
app = Flask(__name__, static_folder=None)

@app.get("/")
def serve_index():
    return send_from_directory(SITE_DIR, "index.html")

@app.get("/<path:path>")
def serve_static(path: str):
    # Serve the static site files.
    return send_from_directory(SITE_DIR, path)

@app.get("/api/log")
def api_log():
    con = db()
    rows = con.execute("SELECT * FROM entries ORDER BY id ASC").fetchall()
    con.close()
    entries = []
    for r in rows:
        entries.append({
            "txid": r["txid"],
            "alias": r["alias"] or "",
            "city": r["city"],
            "country": r["country"],
            "lat": float(r["lat"]),
            "lng": float(r["lng"]),
            "amount_btc": float(r["amount_btc"]),
            "iso_date": r["iso_date"],
        })
    return jsonify({
        "project": {"origin_label": ORIGIN_LABEL, "btc_address": BTC_ADDRESS, "exported_iso": utc_now_iso()},
        "entries": entries
    })

@app.get("/api/stats")
def api_stats():
    bal = None
    try:
        bal = address_balance_btc(BTC_ADDRESS)
    except Exception:
        bal = None
    return jsonify({
        "btc_address": BTC_ADDRESS,
        "origin_label": ORIGIN_LABEL,
        "balance_btc": bal,
        "iso_date": utc_now_iso()
    })

@app.post("/api/submit")
def api_submit():
    ip = request.headers.get("X-Forwarded-For", request.remote_addr) or "unknown"
    if rate_limited(ip):
        return jsonify({"error": "Too many requests. Try again later."}), 429

    data = request.get_json(silent=True) or {}
    txid = sanitize_text(data.get("txid", ""), 64)
    alias = sanitize_text(data.get("alias", ""), ALIAS_MAX)
    city = sanitize_text(data.get("city", ""), CITY_MAX)
    country = sanitize_text(data.get("country", ""), COUNTRY_MAX)

    if not valid_txid(txid):
        return jsonify({"error": "TXID must be a 64-character hex string."}), 400
    if not city or not country:
        return jsonify({"error": "City and country are required."}), 400

    # Verify TX + amount to our address
    try:
        amount_btc = tx_amount_to_our_address_btc(txid, BTC_ADDRESS)
    except Exception:
        amount_btc = None

    if amount_btc is None:
        return jsonify({"error": "Could not verify a payment to the project address for this TXID."}), 400

    # Geocode city+country
    try:
        coords = geocode_city_country(city, country)
    except Exception:
        coords = None
    if coords is None:
        return jsonify({"error": "Could not geocode that city/country. Try a nearby major city."}), 400

    lat, lng = coords
    iso_date = utc_now_iso()

    # Insert
    con = db()
    try:
        con.execute(
            "INSERT INTO entries (txid, alias, city, country, lat, lng, amount_btc, iso_date) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (txid, alias, city, country, lat, lng, amount_btc, iso_date)
        )
        con.commit()
    except sqlite3.IntegrityError:
        con.close()
        return jsonify({"error": "That TXID is already in the log."}), 409
    finally:
        con.close()

    # Export static JSON so the site can work even if API is later disabled
    try:
        export_log_json()
    except Exception:
        pass

    return jsonify({"ok": True})

def main():
    init_db()
    export_log_json()
    port = int(os.environ.get("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

if __name__ == "__main__":
    main()
