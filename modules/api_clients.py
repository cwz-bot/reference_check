# modules/api_clients.py

import streamlit as st
import requests
import urllib.parse
import time
import random
from difflib import SequenceMatcher
from serpapi import GoogleSearch

# 導入清洗函式
from .parsers import clean_title, clean_title_for_remedial

# --- 全域 API 設定 ---
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API_URL = "https://api.openalex.org/works"

# API 參數
MAX_RETRIES = 2
INITIAL_DELAY = 1 
TIMEOUT = 10
TITLE_SIMILARITY_THRESHOLD = 0.90 

# ========== API Key 管理 ==========
def get_scopus_key():
    return st.secrets.get("scopus_api_key") or _read_key_file("scopus_key.txt")

def get_serpapi_key():
    return st.secrets.get("serpapi_key") or _read_key_file("serpapi_key.txt")

def _read_key_file(filename):
    try:
        with open(filename, "r") as f:
            return f.read().strip()
    except FileNotFoundError:
        return None

# --- API 呼叫輔助 (重試機制) ---
def _call_external_api_with_retry(url: str, params: dict, api_name: str) -> dict | None:
    headers = {'User-Agent': 'ReferenceChecker/1.0'}
    
    for attempt in range(MAX_RETRIES):
        delay = INITIAL_DELAY * (2 ** attempt) + random.uniform(0, 1)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            
            if response.status_code == 200:
                return response.json()
            elif response.status_code in [429, 503, 504]:
                time.sleep(delay)
            else:
                return None 
        except requests.exceptions.RequestException:
            time.sleep(delay)
            
    return None

# ========== 1. Crossref (DOI) ==========
def search_crossref_by_doi(doi):
    if not doi: return None, None
    url = f"https://api.crossref.org/works/{doi}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            item = response.json().get("message", {})
            titles = item.get("title", [])
            title = titles[0] if titles else "Unknown Title"
            return title, item.get("URL")
    except:
        pass
    return None, None

# ========== 2. Scopus ==========
def search_scopus_by_title(title, api_key):
    if not api_key or not title: return None
    base_url = "https://api.elsevier.com/content/search/scopus"
    headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}
    clean_q = title.replace('"', '').replace(':', ' ')
    params = {"query": f'TITLE("{clean_q}")', "count": 1}
    
    try:
        response = requests.get(base_url, headers=headers, params=params, timeout=10)
        if response.status_code == 200:
            data = response.json()
            entries = data.get('search-results', {}).get('entry', [])
            if entries:
                return entries[0].get('prism:url', 'https://www.scopus.com')
    except:
        pass
    return None

# ========== 3. Google Scholar (SerpAPI) ==========
def search_scholar_by_title(title, api_key, threshold=0.90):
    if not api_key or not title: return None, "no_result"
    
    params = {
        "engine": "google_scholar",
        "q": title,
        "api_key": api_key,
        "num": 3
    }
    search_url = "https://scholar.google.com"

    try:
        results = GoogleSearch(params).get_dict()
        if "error" in results: return None, "error"
        
        organic = results.get("organic_results", [])
        if not organic: return None, "no_result"
        
        cleaned_query = clean_title(title)

        for result in organic:
            res_title = result.get("title", "")
            res_link = result.get("link")
            cleaned_res = clean_title(res_title)
            
            if cleaned_query == cleaned_res:
                return res_link, "match"
            
            if SequenceMatcher(None, cleaned_query, cleaned_res).ratio() >= threshold:
                return res_link, "similar"

        return search_url, "no_result"
    except Exception:
        return search_url, "error"

def search_scholar_by_ref_text(ref_text, api_key):
    """補救搜尋"""
    if not api_key or not ref_text: return None, "no_result"
    
    params = {"engine": "google_scholar", "q": ref_text, "api_key": api_key, "num": 1}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if organic:
            return organic[0].get("link"), "similar"
    except:
        pass
    return None, "no_result"

# ========== 4. Semantic Scholar ==========
def search_s2_by_title(title):
    if not title: return None
    params = {'query': title, 'limit': 1, 'fields': 'title,url'}
    
    data = _call_external_api_with_retry(S2_API_URL, params, "S2")
    
    if data and data.get('data'):
        match = data['data'][0]
        if _is_match(title, match.get('title')):
            return match.get('url')
    return None

# ========== 5. OpenAlex ==========
def search_openalex_by_title(title):
    if not title: return None
    params = {'search': title, 'per_page': 1, 'select': 'title,doi,id'}
    
    data = _call_external_api_with_retry(OPENALEX_API_URL, params, "OpenAlex")
    
    if data and data.get('results'):
        match = data['results'][0]
        if _is_match(title, match.get('title')):
            return match.get('doi') or match.get('id')
    return None

def _is_match(query, result):
    if not query or not result: return False
    c_q = clean_title(query)
    c_r = clean_title(result)
    if c_q == c_r: return True
    return SequenceMatcher(None, c_q, c_r).ratio() >= TITLE_SIMILARITY_THRESHOLD