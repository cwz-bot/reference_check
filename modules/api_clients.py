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

# 將門檻調至 1.0 (代表必須完全相同)
TITLE_SIMILARITY_THRESHOLD = 1.0 

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
def _call_external_api_with_retry(url: str, params: dict, api_name: str):
    headers = {'User-Agent': 'ReferenceChecker/1.0'}
    last_error = "Unknown"
    
    for attempt in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if response.status_code == 200:
                return response.json(), "OK"
            elif response.status_code == 429:
                last_error = "Rate Limit (429)"
                time.sleep(2)
            elif response.status_code in [401, 403]:
                return None, f"Auth Error ({response.status_code})"
            else:
                last_error = f"HTTP {response.status_code}"
        except Exception as e:
            last_error = f"Conn Error: {type(e).__name__}"
            
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
        return None, None, str(e)

def search_crossref_by_text(title, author=None):
    if not title: return None, "Empty Title"
    params = {'query.bibliographic': title, 'rows': 1, 'select': 'title,URL,DOI'}
    data, status = _call_external_api_with_retry("https://api.crossref.org/works", params, "Crossref-Text")
    if status != "OK": return None, status
    
    if data and data.get('message', {}).get('items'):
        item = data['message']['items'][0]
        res_title = item.get('title', [''])[0]
        if _is_match(title, res_title):
            return item.get('URL') or f"https://doi.org/{item.get('DOI')}", "OK"
        return None, "Match failed (Below Threshold)"
    return None, "Not Found"

# ========== 2. Scopus ==========
def search_scopus_by_title(title, api_key):
    if not api_key: return None, "No API Key"
    base_url = "https://api.elsevier.com/content/search/scopus"
    headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}
    params = {"query": f'TITLE("{title}")', "count": 1}
    
    data, status = _call_external_api_with_retry(base_url, params, "Scopus")
    if status != "OK": return None, status
    
    if data:
        entries = data.get('search-results', {}).get('entry', [])
        if entries and 'error' not in entries[0]:
            return entries[0].get('prism:url', 'https://www.scopus.com'), "OK"
    return None, "Not Found"

# ========== 3. Google Scholar (SerpAPI) ==========
def search_scholar_by_title(title, api_key):
    if not api_key: return None, "No API Key"
    params = {"engine": "google_scholar", "q": title, "api_key": api_key, "num": 3}
    try:
        results = GoogleSearch(params).get_dict()
        if "error" in results: return None, f"SerpAPI Error: {results['error']}"
        
        organic = results.get("organic_results", [])
        for result in organic:
            res_title = result.get("title", "")
            if _is_match(title, res_title):
                return result.get("link"), "match"
        return None, "No exact match found"
    except Exception as e:
        return None, f"Exception: {str(e)}"

def search_scholar_by_ref_text(ref_text, api_key, target_title=None):
    if not api_key: return None, "No API Key"
    params = {"engine": "google_scholar", "q": ref_text, "api_key": api_key, "num": 1}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if organic:
            res_title = organic[0].get("title", "")
            # 補救搜尋也要過比對，避免抓到不相干的論文
            if target_title and not _is_match(target_title, res_title):
                return None, "Title mismatch in fallback"
            return organic[0].get("link"), "similar"
    except: pass
    return None, "No results"

# ========== 4. Semantic Scholar ==========
def search_s2_by_title(title, author=None):
    params = {'query': title, 'limit': 1, 'fields': 'title,url'}
    data, status = _call_external_api_with_retry(S2_API_URL, params, "S2")
    if status == "OK" and data.get('data'):
        match = data['data'][0]
        if _is_match(title, match.get('title')):
            return match.get('url'), "OK"
    return None, status

# ========== 5. OpenAlex ==========
# modules/api_clients.py 中的 OpenAlex 部分

def search_openalex_by_title(title, author=None):
    if not title: return None, "Empty Title"
    params = {'search': title, 'per_page': 1, 'select': 'title,doi,id,ids'}
    data, status = _call_external_api_with_retry(OPENALEX_API_URL, params, "OpenAlex")
    
    if status != "OK": return None, status
    
    if data and data.get('results'):
        match = data['results'][0]
        if _is_match(title, match.get('title')):
            # 優先順序：DOI -> OpenAlex ID 網址 -> 原始 ID
            doi = match.get('doi')
            if doi:
                return doi, "OK"
            
            # 如果沒有 DOI，嘗試抓取 OpenAlex 自己的展示頁面連結
            oa_id = match.get('id')
            if oa_id:
                # OpenAlex ID 通常是 https://openalex.org/W... 格式，可以直接點擊
                return oa_id, "OK"
                
            return None, "OK" # 標題對了但真的沒連結
    return None, "Not Found"

# ========== 核心比對邏輯 (超嚴格版) ==========
def _is_match(query, result):
    if not query or not result: return False
    c_q = clean_title(query)
    c_r = clean_title(result)
    
    # 1. 計算相似度
    ratio = SequenceMatcher(None, c_q, c_r).ratio()
    
    # 2. 核心關鍵字檢查 (即便相似度很高，只要重要單字不見了就視為失敗)
    q_words = set(c_q.split())
    r_words = set(c_r.split())
    
    # 找出 query 中存在但 result 中沒有的單字
    missing_important = []
    # 排除掉這些無意義的連接詞
    stop_words = {'a', 'an', 'the', 'of', 'in', 'for', 'with', 'on', 'at', 'by', 'and', 'model', 'based', 'using'}
    
    for word in q_words:
        if word not in stop_words and word not in r_words:
            missing_important.append(word)

    # 只要有一個核心關鍵字 (如 "time", "series") 沒對上，就回傳 False
    if missing_important:
        return False

    return ratio >= TITLE_SIMILARITY_THRESHOLD

def check_url_availability(url):
    if not url or not url.startswith("http"): return False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        response = requests.head(url, timeout=5, allow_redirects=True, verify=False)
        return 200 <= response.status_code < 400
    except: return False