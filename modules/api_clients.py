# modules/api_clients.py
import streamlit as st
import requests
import time
from difflib import SequenceMatcher
from serpapi import GoogleSearch
import urllib3

# 導入標題清洗函式
from .parsers import clean_title

# --- 全域 API 設定 ---
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API_URL = "https://api.openalex.org/works"

MAX_RETRIES = 2
TIMEOUT = 10
# 強制設定為 1.0 達成完全匹配，避免相似標題誤判
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

# ========== 核心比對邏輯 (全資料庫防誤判版) ==========
def _is_match(query, result):
    if not query or not result: return False
    c_q = clean_title(query)
    c_r = clean_title(result)
    
    # 第一關：字元相似度
    ratio = SequenceMatcher(None, c_q, c_r).ratio()
    
    # 第二關：核心關鍵字過濾
    q_words = set(c_q.split())
    r_words = set(c_r.split())
    # 排除虛詞
    stop_words = {'a', 'an', 'the', 'of', 'in', 'for', 'with', 'on', 'at', 'by', 'and', 'model', 'based', 'using', 'analysis', 'study'}
    
    # 只要 query 裡的核心單字 (如 time, series) 在結果裡不見了，直接判定失敗
    missing_important = [w for w in q_words if w not in stop_words and w not in r_words]
    
    if missing_important:
        return False

    return ratio >= TITLE_SIMILARITY_THRESHOLD

# --- API 呼叫輔助 ---
def _call_external_api_with_retry(url: str, params: dict, headers=None):
    if not headers: headers = {'User-Agent': 'ReferenceChecker/1.0'}
    for _ in range(MAX_RETRIES):
        try:
            response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            if response.status_code == 200: return response.json(), "OK"
            if response.status_code in [401, 403]: return None, f"Auth Error ({response.status_code})"
        except: pass
    return None, "Error"

# ========== 1. Crossref (含校驗功能) ==========

def search_crossref_by_doi(doi, target_title=None):
    """
    透過 DOI 搜尋，並核對回傳標題是否與目標吻合
    """
    if not doi: return None, None, "Empty DOI"
    clean_doi = doi.strip(' ,.;)]}>')
    url = f"https://api.crossref.org/works/{clean_doi}"
    try:
        response = requests.get(url, timeout=5)
        if response.status_code == 200:
            item = response.json().get("message", {})
            titles = item.get("title", [])
            res_title = titles[0] if titles else ""
            
            # 防誤判：標題不對就攔截
            if target_title and not _is_match(target_title, res_title):
                return None, None, f"DOI Title Mismatch: {res_title[:40]}..."
                
            return res_title, item.get("URL") or f"https://doi.org/{clean_doi}", "OK"
        return None, None, f"HTTP {response.status_code}"
    except: return None, None, "Conn Error"

def search_crossref_by_text(title, author=None):
    """
    補回原本缺失的函式：透過標題文字搜尋 Crossref
    """
    if not title: return None, "Empty Title"
    params = {'query.bibliographic': title, 'rows': 1}
    data, status = _call_external_api_with_retry("https://api.crossref.org/works", params)
    
    if status == "OK" and data and data.get('message', {}).get('items'):
        item = data['message']['items'][0]
        res_title = item.get('title', [''])[0]
        if _is_match(title, res_title):
            return item.get('URL') or f"https://doi.org/{item.get('DOI')}", "OK"
        return None, "Match failed (Below Threshold)"
    return None, status

# ========== 2. Scopus ==========

def search_scopus_by_title(title, api_key):
    if not api_key: return None, "No API Key"
    url = "https://api.elsevier.com/content/search/scopus"
    headers = {"Accept": "application/json", "X-ELS-APIKey": api_key}
    params = {"query": f'TITLE("{title}")', "count": 1}
    data, status = _call_external_api_with_retry(url, params, headers)
    # 修改 modules/api_clients.py 中的 Scopus 部分
    if status == "OK" and data:
        entries = data.get('search-results', {}).get('entry', [])
        if not entries or 'error' in entries[0]:
            return None, "(No results found)" # 修改這裡：明確標示沒找到
        
        res_title = entries[0].get('dc:title', '')
        if _is_match(title, res_title):
            return entries[0].get('prism:url', 'https://www.scopus.com'), "OK"
        else:
            return None, f"OK (Title Mismatch: {res_title[:30]}...)" # 明確標示標題不符

# ========== 3. Google Scholar ==========

def search_scholar_by_title(title, api_key):
    if not api_key: return None, "No API Key"
    params = {"engine": "google_scholar", "q": title, "api_key": api_key, "num": 3}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        for res in organic:
            if _is_match(title, res.get("title", "")):
                return res.get("link"), "match"
        return None, "No exact match found"
    except Exception as e: return None, str(e)

def search_scholar_by_ref_text(ref_text, api_key, target_title=None):
    if not api_key: return None, "No API Key"
    params = {"engine": "google_scholar", "q": ref_text, "api_key": api_key, "num": 1}
    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if organic:
            res_title = organic[0].get("title", "")
            if target_title and not _is_match(target_title, res_title):
                return None, "Title mismatch in fallback"
            return organic[0].get("link"), "similar"
    except: pass
    return None, "No results"

# ========== 4. Semantic Scholar & OpenAlex ==========

def search_s2_by_title(title, author=None):
    params = {'query': title, 'limit': 1, 'fields': 'title,url'}
    data, status = _call_external_api_with_retry(S2_API_URL, params)
    if status == "OK" and data.get('data'):
        match = data['data'][0]
        res_title = match.get('title')
        res_url = match.get('url')

        if _is_match(title, res_title):
            if res_url: # 確保有網址
                return res_url, "OK"
            return None, "No URL found for this match"
        return None, "Match failed"
    return None, status

def search_openalex_by_title(title, author=None):
    params = {'search': title, 'per_page': 1}
    data, status = _call_external_api_with_retry(OPENALEX_API_URL, params)
    
    if status == "OK" and data.get('results'):
        match = data['results'][0]
        if _is_match(title, match.get('title')):
            # 取得連結，若兩者皆無則為 None
            url = match.get('doi') or match.get('id')
            
            # --- 修改重點：檢查是否有連結 ---
            if url:
                return url, "OK"
            else:
                return None, "No Link Available (Found title but no URL)"
        else:
            return None, "Match failed (Title Mismatch)"
            
    return None, status if status != "OK" else "No results found"

def check_url_availability(url):
    if not url or not url.startswith("http"): return False
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    try:
        resp = requests.head(url, timeout=5, allow_redirects=True, verify=False)
        return 200 <= resp.status_code < 400
    except: return False