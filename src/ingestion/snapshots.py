"""Strict historical station inventory parsing shared by ingestion and audit."""

import json
import re

import pandas as pd


def parse_snapshot_bytes(data, source):
    """Read every JSONL snapshot, retaining inventory fields and original naive time.

    No timezone is invented for offset-free source timestamps. Bad JSON is an
    explicit extraction failure, not a silently dropped sample.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        # Legacy archives encode Spanish names as single-byte Latin-1.
        # Preserve the original characters instead of inserting replacement glyphs.
        text = data.decode("latin-1")
    try:
        payloads = [json.loads(text)]
    except json.JSONDecodeError:
        # July 2018 is a Mongo shell export: pretty-printed adjacent objects
        # with NumberInt literals. Normalize only that numeric wrapper outside
        # quoted strings; never evaluate source code or rewrite string values.
        tokens = re.compile(r'"(?:\\.|[^"\\])*"|NumberInt\(\s*(?P<integer>-?\d+)\s*\)')
        text = tokens.sub(lambda match: match.group("integer") or match.group(0), text)
        decoder = json.JSONDecoder()
        whitespace = re.compile(r"\s*")
        offset = 0
        payloads = []
        while offset < len(text):
            offset = whitespace.match(text, offset).end()
            if offset >= len(text):
                break
            payload, offset = decoder.raw_decode(text, offset)
            payloads.append(payload)
    rows = []
    for payload in payloads:
        if not isinstance(payload, dict) or not isinstance(payload.get("stations"), list):
            raise TypeError(f"Unexpected snapshot schema: {source}")
        for station in payload["stations"]:
            rows.append(
                {
                    "station": str(station.get("id", station.get("number", ""))),
                    "time": payload.get("_id"),
                    "bikes": station.get("dock_bikes"),
                    "capacity": station.get("total_bases"),
                    "free": station.get("free_bases"),
                    "active": station.get("activate"),
                    "unavailable": station.get("no_available"),
                    "name": station.get("name"),
                    "station_number": station.get("number"),
                    "address": station.get("address"),
                    "latitude": station.get("latitude"),
                    "longitude": station.get("longitude"),
                }
            )
    return pd.DataFrame(rows)
