# modules/api_clients.py

import streamlit as st
import requests
import urllib.parse
import time
import random
import urllib3
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
TITLE_SIMILARITY_THRESHOLD = 0.85 

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

# --- API 呼叫輔助 (重試機制強化版) ---
def _call_external_api_with_retry(url: str, params: dict, api_name: str):
    headers = {'User-Agent': 'ReferenceChecker/1.0 (mailto:admin@example.com)'}
    last_error = "Unknown"
    
    for attempt in range(MAX_RETRIES):
        delay = INITIAL_DELAY * (2 ** attempt) + random.uniform(0, 1)
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            
            if response.status_code == 200:
                return response.json(), "OK"
            elif response.status_code == 429:
                last_error = f"Rate Limit (429)"
                time.sleep(delay)
            elif response.status_code in [401, 403]:
                return None, f"Auth Error ({response.status_code})"
            else:
                last_error = f"HTTP Error {response.status_code}"
                time.sleep(delay)
        except requests.exceptions.RequestException as e:
            last_error = f"Conn Error: {type(e).__name__}"
            time.sleep(delay)
            
    return None, last_error

# ========== 1. Crossref (DOI & Text Search) ==========
def search_crossref_by_doi(doi):
    if not doi: return None, None, "Empty DOI"
    clean_doi = doi.strip(' ,.;)]}>')
    url = f"https://api.crossref.org/works/{clean_doi}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            item = response.json().get("message", {})
            titles = item.get("title", [])
            title = titles[0] if titles else "Unknown Title"
            return title, item.get("URL") or f"https://doi.org/{clean_doi}", "OK"
        return None, None, f"HTTP {response.status_code}"
    except Exception as e:
        return None, None, f"Exception: {str(e)}"

def search_crossref_by_text(title, author=None):
    if not title: return None, "Empty Title"
    params = {'query.bibliographic': title, 'rows': 1, 'select': 'title,URL,DOI,score'}
    if author and len(title) < 20:
        params['query.author'] = author
    
    data, status = _call_external_api_with_retry("https://api.crossref.org/works", params, "Crossref-Text")
    if status != "OK": return None, status
    
    if data and data.get('message', {}).get('items'):
        item = data['message']['items'][0]
        res_title = item.get('title', [''])[0]
        if _is_match(title, res_title):
            return item.get('URL') or f"https://doi.org/{item.get('DOI')}", "OK"
        return None, "Match score below threshold"
    return None, "No results found"

# ========== 2. Scopus ==========
def search_scopus_by_title(title, api_key):
    if not api_key: return None, "No API Key"
    if not title: return None, "Empty Title"
    base_url = "https://api.elsevier.com/content/search/scopus"
    headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}
    clean_q = title.replace('"', '').replace(':', ' ')
    params = {"query": f'TITLE("{clean_q}")', "count": 1}
    
    data, status = _call_external_api_with_retry(base_url, params, "Scopus")
    if status != "OK": return None, status
    
    if data:
        entries = data.get('search-results', {}).get('entry', [])
        if entries and 'error' not in entries[0]:
            return entries[0].get('prism:url', 'https://www.scopus.com'), "OK"
        return None, "Title not found in Scopus"
    return None, "Empty response"

# ========== 3. Google Scholar (SerpAPI) ==========
def search_scholar_by_title(title, api_key, threshold=0.85):
    if not api_key: return None, "No API Key"
    if not title: return None, "Empty Title"
    
    params = {"engine": "google_scholar", "q": title, "api_key": api_key, "num": 3}
    try:
        results = GoogleSearch(params).get_dict()
        if "error" in results: return None, f"SerpAPI Error: {results['error']}"
        
        organic = results.get("organic_results", [])
        if not organic: return None, "No organic results"
        
        cleaned_query = clean_title(title)
        for result in organic:
            res_title = result.get("title", "")
            res_link = result.get("link")
            cleaned_res = clean_title(res_title)
            if cleaned_query == cleaned_res: return res_link, "match"
            if SequenceMatcher(None, cleaned_query, cleaned_res).ratio() >= threshold:
                return res_link, "similar"
        return None, "No similar titles found"
    except Exception as e:
        return None, f"Exception: {str(e)}"

def search_scholar_by_ref_text(ref_text, api_key, target_title=None):
    """
    補救搜尋：增加標題二次驗證，避免回傳不相關的論文。
    """
    if not api_key or not ref_text: 
        return None, "No input"
    
    params = {"engine": "google_scholar", "q": ref_text, "api_key": api_key, "num": 1}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        
        if organic:
            res_title = organic[0].get("title", "")
            res_link = organic[0].get("link")
            
            # 如果有提供目標標題，進行相似度檢查
            if target_title:
                c_target = clean_title(target_title)
                c_res = clean_title(res_title)
                similarity = SequenceMatcher(None, c_target, c_res).ratio()
                
                # 門檻設在 0.6，避免像圖二那種標題完全不同的論文過關
                if similarity < 0.6:
                    return None, f"Similar result found but title mismatch ({similarity:.2f})"
            
            return res_link, "similar"
    except Exception as e:
        return None, f"Exception: {str(e)}"
    
    return None, "No results"

# ========== 4. Semantic Scholar ==========
def search_s2_by_title(title, author=None):
    if not title: return None, "Empty Title"
    params = {'query': title, 'limit': 1, 'fields': 'title,url'}
    data, status = _call_external_api_with_retry(S2_API_URL, params, "S2")
    if status != "OK": return None, status
    
    if data and data.get('data'):
        match = data['data'][0]
        if _is_match(title, match.get('title')): return match.get('url'), "OK"
        return None, "Similarity below threshold"
    return None, "Not Found"

# ========== 5. OpenAlex ==========
def search_openalex_by_title(title, author=None):
    if not title: return None, "Empty Title"
    params = {'search': title, 'per_page': 1, 'select': 'title,doi,id'}
    data, status = _call_external_api_with_retry(OPENALEX_API_URL, params, "OpenAlex")
    if status != "OK": return None, status
    
    if data and data.get('results'):
        match = data['results'][0]
        if _is_match(title, match.get('title')):
            return (match.get('doi') or match.get('id')), "OK"
        return None, "Similarity below threshold"
    return None, "Not Found"

def _is_match(query, result):
    if not query or not result: return False
    c_q = clean_title(query)
    c_r = clean_title(result)
    if c_q in c_r or c_r in c_q:
        len_diff = abs(len(c_q) - len(c_r))
        if len_diff / max(len(c_q), len(c_r)) < 0.4: return True
    return SequenceMatcher(None, c_q, c_r).ratio() >= TITLE_SIMILARITY_THRESHOLD

def check_url_availability(url):
    if not url or not url.startswith("http"): return False
    headers = {'User-Agent': 'Mozilla/5.0'}
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        response = requests.head(url, headers=headers, timeout=5, allow_redirects=True, verify=False)
        if 200 <= response.status_code < 400: return True
        response = requests.get(url, headers=headers, timeout=8, stream=True, verify=False)
        if 200 <= response.status_code < 400: return True
    except: pass
    return False