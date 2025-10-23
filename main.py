# main.py
from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel
from typing import Dict, Any, Optional, List
from datetime import datetime, timezone
import hashlib
from collections import Counter
import json
import re
import os

# -------------- Config --------------
PERSIST_FILE = "strings_store.json"  # set to None to disable file persistence

# -------------- In-memory storage --------------
# key = sha256_hash, value = stored object
strings_db: Dict[str, Dict[str, Any]] = {}

# Try load persistence on startup
if PERSIST_FILE and os.path.exists(PERSIST_FILE):
    try:
        with open(PERSIST_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
            strings_db.update({k: v for k, v in data.items()})
            print(f"Loaded {len(strings_db)} entries from {PERSIST_FILE}")
    except Exception as e:
        print("Failed to load persistence file:", e)

def persist_db():
    if not PERSIST_FILE:
        return
    try:
        with open(PERSIST_FILE, "w", encoding="utf-8") as f:
            json.dump(strings_db, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print("Failed to persist DB:", e)

# -------------- Helpers --------------
def sha256_hash(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()

def iso_now():
    return datetime.now(timezone.utc).isoformat()

def normalize_for_palindrome(s: str) -> str:
    """Lowercase and remove non-alphanumeric for palindrome checks."""
    return re.sub(r'[^a-z0-9]', '', s.lower())

def compute_properties(value: str) -> Dict[str, Any]:
    length = len(value)
    normalized = normalize_for_palindrome(value)
    is_palindrome = normalized == normalized[::-1] and normalized != ""
    word_count = len(value.split())
    lowered = value.lower()
    freq_map = dict(Counter(lowered))
    unique_characters = len(freq_map)
    return {
        "length": length,
        "is_palindrome": is_palindrome,
        "unique_characters": unique_characters,
        "word_count": word_count,
        "character_frequency_map": freq_map
    }

# -------------- FastAPI models & app --------------
class CreateRequest(BaseModel):
    value: str

app = FastAPI(title="String Analyzer Service - Stage 1")

# -------------- Endpoints --------------

@app.post("/strings", status_code=201)
def create_string(payload: CreateRequest):
    if not isinstance(payload.value, str):
        raise HTTPException(status_code=422, detail="Field 'value' must be a string.")

    raw_value = payload.value
    sid = sha256_hash(raw_value)

    if sid in strings_db:
        raise HTTPException(status_code=409, detail="String already exists.")

    props = compute_properties(raw_value)
    props["sha256_hash"] = sid

    entry = {
        "id": sid,
        "value": raw_value,
        "properties": props,
        "created_at": iso_now()
    }

    strings_db[sid] = entry
    persist_db()
    return entry

@app.get("/strings")
def list_strings(
    is_palindrome: Optional[bool] = Query(None),
    min_length: Optional[int] = Query(None, ge=0),
    max_length: Optional[int] = Query(None, ge=0),
    word_count: Optional[int] = Query(None, ge=0),
    contains_character: Optional[str] = Query(None, min_length=1, max_length=1)
):
    results: List[Dict[str, Any]] = list(strings_db.values())

    if is_palindrome is not None:
        results = [r for r in results if r["properties"]["is_palindrome"] == is_palindrome]
    if min_length is not None:
        results = [r for r in results if r["properties"]["length"] >= min_length]
    if max_length is not None:
        results = [r for r in results if r["properties"]["length"] <= max_length]
    if word_count is not None:
        results = [r for r in results if r["properties"]["word_count"] == word_count]
    if contains_character is not None:
        ch = contains_character.lower()
        results = [r for r in results if ch in r["properties"]["character_frequency_map"]]

    return {
        "data": results,
        "count": len(results),
        "filters_applied": {
            "is_palindrome": is_palindrome,
            "min_length": min_length,
            "max_length": max_length,
            "word_count": word_count,
            "contains_character": contains_character
        }
    }

def parse_nl_query(q: str) -> Dict[str, Any]:
    q_lower = q.lower()
    parsed = {}
    if "palind" in q_lower:
        parsed["is_palindrome"] = True
    if "single word" in q_lower or "one word" in q_lower:
        parsed["word_count"] = 1
    m = re.search(r"longer than (\d+)", q_lower)
    if m:
        parsed["min_length"] = int(m.group(1)) + 1
    m2 = re.search(r"containing the letter (\w)", q_lower)
    if m2:
        parsed["contains_character"] = m2.group(1).lower()
    m3 = re.search(r"containing (\w)", q_lower)
    if m3 and "containing the letter" not in q_lower:
        parsed["contains_character"] = m3.group(1).lower()
    return parsed

@app.get("/strings/filter-by-natural-language")
def filter_by_nl(query: str):
    parsed = parse_nl_query(query)
    if not parsed:
        raise HTTPException(status_code=400, detail="Unable to parse natural language query.")

    filtered = list_strings(
        is_palindrome=parsed.get("is_palindrome"),
        min_length=parsed.get("min_length"),
        max_length=parsed.get("max_length"),
        word_count=parsed.get("word_count"),
        contains_character=parsed.get("contains_character")
    )

    data = filtered.get("data", [])
    return {
        "data": data,
        "count": len(data),
        "interpreted_query": {
            "original": query,
            "parsed_filters": parsed
        }
    }

@app.get("/strings/{string_value}")
def get_string(string_value: str):
    sid = sha256_hash(string_value)
    entry = strings_db.get(sid)
    if not entry:
        raise HTTPException(status_code=404, detail="String not found.")
    return entry

@app.delete("/strings/{string_value}", status_code=204)
def delete_string(string_value: str):
    sid = sha256_hash(string_value)
    if sid not in strings_db:
        raise HTTPException(status_code=404, detail="String not found.")
    del strings_db[sid]
    persist_db()
    return {}
