

# main.py
from fastapi import FastAPI, HTTPException, Query, Request, Response
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
    # Note: This is the standard definition for phrase palindromes
    return re.sub(r'[^a-z0-9]', '', s.lower())

def compute_properties(value: str) -> Dict[str, Any]:
    length = len(value)
    normalized = normalize_for_palindrome(value)
    # Palindrome is true only if normalized string is non-empty and reads the same forwards and backwards
    is_palindrome = normalized == normalized[::-1] and normalized != ""
    word_count = len(value.split())
    
    # Character frequency map and unique count are case-insensitive
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
async def create_string(request: Request):
    """
    Handles string creation and analysis. Enforces required HTTP status codes.
    """
    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON body.")

    # 400 Bad Request: Missing 'value'
    if "value" not in payload:
        raise HTTPException(status_code=400, detail="Missing 'value' field.")

    value = payload["value"]

    # 422 Unprocessable Entity: Wrong type
    if not isinstance(value, str):
        raise HTTPException(status_code=422, detail="Field 'value' must be a string.")

    sid = sha256_hash(value)

    # 409 Conflict: Duplicate
    if sid in strings_db:
        raise HTTPException(status_code=409, detail="String already exists.")

    props = compute_properties(value)
    props["sha256_hash"] = sid

    entry = {
        "id": sid,
        "value": value,
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
    # min_length=1, max_length=1 enforces a single character
    contains_character: Optional[str] = Query(None, min_length=1, max_length=1) 
):
    """Retrieves all strings with optional filtering."""
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
        # Case-insensitive check against the character_frequency_map keys (which are lowercase)
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
    """
    Parses natural language query into concrete filter parameters.
    FIXED: Improved regex and added 'first vowel' support.
    """
    q_lower = q.lower()
    parsed = {}
    
    # 1. Palindrome Check
    if "palindromic" in q_lower or "palindrome" in q_lower:
        parsed["is_palindrome"] = True
    
    # 2. Word Count Check
    if "single word" in q_lower or "one word" in q_lower:
        parsed["word_count"] = 1
    
    # 3. Length Check (e.g., "longer than 10 characters")
    m_len = re.search(r"(longer|shorter) than\s*(\d+)", q_lower)
    if m_len:
        comparison = m_len.group(1)
        number = int(m_len.group(2))
        if comparison == "longer":
            # "longer than 10" -> min_length=11
            parsed["min_length"] = number + 1
        elif comparison == "shorter":
            # "shorter than 10" -> max_length=9 (not explicitly required but robust)
            parsed["max_length"] = number - 1
            
    # 4. Contains Character Check (e.g., "the first vowel", "the letter z")
    contains_char_val = None
    
    # a. Specific case: "first vowel" -> 'a'
    if "first vowel" in q_lower:
        contains_char_val = 'a'
    
    # b. General case: "containing the letter X" or "containing X"
    # Capture a single word (e.g., 'z' or 'a') following "letter" or "containing"
    m_char = re.search(r"(?:containing|contains) (?:the letter |a letter |the |a |of |)(?:\'|\")?(\w)(?:\'|\")?", q_lower)
    if m_char:
        char = m_char.group(1).lower()
        # Only use this if not already set by a stronger rule (like 'first vowel')
        if contains_char_val is None and len(char) == 1:
             contains_char_val = char

    if contains_char_val:
        parsed["contains_character"] = contains_char_val

    return parsed


@app.get("/strings/filter-by-natural-language")
def filter_by_nl(query: str):
    """Performs filtering based on a natural language query."""
    parsed = parse_nl_query(query)
    
    # 400 Bad Request: Unable to parse
    if not parsed:
        raise HTTPException(status_code=400, detail="Unable to parse natural language query.")

    # 422 Unprocessable Entity: Conflicting filters (basic check)
    if parsed.get("min_length") is not None and parsed.get("max_length") is not None:
        if parsed["min_length"] > parsed["max_length"]:
            raise HTTPException(status_code=422, detail="Query parsed but resulted in conflicting length filters.")
            
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
    """Retrieves a specific string by its value (hashed to ID)."""
    sid = sha256_hash(string_value)
    entry = strings_db.get(sid)
    # 404 Not Found
    if not entry:
        raise HTTPException(status_code=404, detail="String not found.")
    return entry

@app.delete("/strings/{string_value}", status_code=204)
def delete_string(string_value: str):
    """Deletes a specific string by its value."""
    sid = sha256_hash(string_value)
    # 404 Not Found
    if sid not in strings_db:
        raise HTTPException(status_code=404, detail="String not found.")
    del strings_db[sid]
    persist_db()
    # return empty 204 response
    return Response(status_code=204)
