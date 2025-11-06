# modules/api_clients.py

import streamlit as st
import requests
import urllib.parse
import time # 新增：用於延遲和重試
import random # 新增：用於延遲和重試

from serpapi import GoogleSearch
from difflib import SequenceMatcher # [!] 保持或新增：用於相似度比對

# 從我們自己的模組導入
from .parsers import clean_title, clean_title_for_remedial

# --- 全域 API 設定 ---
S2_API_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
OPENALEX_API_URL = "https://api.openalex.org/works"

# API 呼叫設定 (沿用舊單檔案中的設定，但簡化)
MAX_RETRIES = 3 
INITIAL_DELAY = 2 
TIMEOUT = 10
# [新增] 相似度門檻值
TITLE_SIMILARITY_THRESHOLD = 0.90 # 相似度需達 90% 才視為命中

# ========== API Key 管理 ==========
def get_scopus_key():
    try:
        return st.secrets["scopus_api_key"]
    except Exception:
        try:
            with open("scopus_key.txt", "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            st.error("❌ 找不到 Scopus API 金鑰，請確認已設定 secrets 或提供 scopus_key.txt")
            st.stop()

def get_serpapi_key():
    try:
        return st.secrets["serpapi_key"]
    except Exception:
        try:
            with open("serpapi_key.txt", "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            st.error("❌ 找不到 SerpAPI 金鑰，請確認已設定 secrets 或提供 serpapi_key.txt")
            st.stop()

# --- 輔助函數：API 帶重試呼叫 (為 S2/OpenAlex 新增) ---
def _call_external_api_with_retry(url: str, params: dict, api_name: str, method='GET') -> dict | None:
    headers = {
        'User-Agent': 'ReferenceChecker (Streamlit App)'
    }

    for attempt in range(MAX_RETRIES):
        delay = INITIAL_DELAY * (2 ** attempt) + random.uniform(0, 1)
        try:
            if method == 'GET':
                response = requests.get(url, params=params, headers=headers, timeout=TIMEOUT)
            else:
                response = requests.post(url, json=params, headers=headers, timeout=TIMEOUT)

            if response.status_code == 200:
                return response.json()
            elif response.status_code in [429, 503, 504]:
                time.sleep(delay)
            else:
                return None # 不可重試的錯誤

        except requests.exceptions.Timeout:
            time.sleep(delay)
        except requests.exceptions.RequestException:
            return None

    return None

# ========== Crossref DOI 查詢 (不變) ==========
def search_crossref_by_doi(doi):
    url = f"https://api.crossref.org/works/{doi}"
    response = requests.get(url)
    if response.status_code == 200:
        item = response.json().get("message", {})
        titles = item.get("title")
        if isinstance(titles, list) and len(titles) > 0:
            # 這裡我們只返回標題和 URL，不執行分數判斷
            return titles[0], item.get("URL")
        else:
            return None, item.get("URL")
    return None, None

# ========== Scopus 查詢 (不變) ==========
def search_scopus_by_title(title, api_key):
    base_url = "https://api.elsevier.com/content/search/scopus"
    headers = {
        "Accept": "application/json",
        "X-ELS-APIKey": api_key
    }
    params = {
        "query": f'TITLE("{title}")',
        "count": 3
    }
    response = requests.get(base_url, headers=headers, params=params)
    if response.status_code == 200:
        data = response.json()
        entries = data.get('search-results', {}).get('entry', [])
        for entry in entries:
            doc_title = entry.get('dc:title', '')
            if doc_title.strip().lower() == title.strip().lower():
                return entry.get('prism:url', 'https://www.scopus.com')
    return None

# ========== Serpapi 查詢 (不變) ==========
def search_scholar_by_title(title, api_key, threshold=0.90):
    search_url = f"https://scholar.google.com/scholar?q={urllib.parse.quote(title)}"
    params = {
        "engine": "google_scholar",
        "q": title,
        "api_key": api_key,
        "num": 3
    }

    try:
        results = GoogleSearch(params).get_dict()

        if "error" in results:
            error_msg = results["error"]
            st.session_state["serpapi_error"] = error_msg
            return search_url, "error"

        organic = results.get("organic_results", [])
        if not organic:
            return search_url, "no_result"

        cleaned_query = clean_title(title)
        for result in organic:
            result_title = result.get("title", "")
            cleaned_result = clean_title(result_title)

            if not cleaned_query or not cleaned_result:
                continue

            if cleaned_query == cleaned_result:
                return search_url, "match"
            if SequenceMatcher(None, cleaned_query, cleaned_result).ratio() >= threshold:
                return search_url, "similar"

        return search_url, "no_result"

    except Exception as e:
        st.session_state["serpapi_error"] = f"API 查詢錯誤：{e}"
        return search_url, "error"


#補救搜尋
def search_scholar_by_ref_text(ref_text, api_key):
    search_url = f"https://scholar.google.com/scholar?q={urllib.parse.quote(ref_text)}"
    params = {
        "engine": "google_scholar",
        "q": ref_text,
        "api_key": api_key,
        "num": 1
    }

    try:
        results = GoogleSearch(params).get_dict()
        organic = results.get("organic_results", [])
        if not organic:
            return search_url, "no_result"

        first_title = organic[0].get("title", "")

        # 使用乾淨版清洗（不影響主流程）
        cleaned_ref = clean_title_for_remedial(ref_text)
        cleaned_first = clean_title_for_remedial(first_title)

        if cleaned_first in cleaned_ref or cleaned_ref in cleaned_first:
            return search_url, "remedial"

        return search_url, "no_result"

    except Exception as e:
        return search_url, "no_result"

# =======================================================================================
# ========== [修改] Semantic Scholar (S2) 查詢：加入相似度檢查 ==========
# =======================================================================================
def search_s2_by_title(title: str) -> str | None:
    """
    使用 Semantic Scholar API 查詢文獻。
    如果找到結果且標題相似度高，返回 S2 連結。
    """
    params = {
        'query': title,
        'limit': 1,
        'fields': 'title,url'
    }

    result = _call_external_api_with_retry(S2_API_URL, params, "S2")

    if result and result.get('data'):
        match = result['data'][0]
        s2_title = match.get('title')
        s2_url = match.get('url')

        if s2_title and s2_url:
            # [新增] 執行相似度檢查
            cleaned_query = clean_title(title)
            cleaned_s2 = clean_title(s2_title)

            if not cleaned_query or not cleaned_s2:
                 return None

            # 精確匹配或相似度達門檻
            if cleaned_query == cleaned_s2 or SequenceMatcher(None, cleaned_query, cleaned_s2).ratio() >= TITLE_SIMILARITY_THRESHOLD:
                return s2_url
    
    return None

# =======================================================================================
# ========== [修改] OpenAlex 查詢：加入相似度檢查 ==========
# =======================================================================================
def search_openalex_by_title(title: str) -> str | None:
    """
    使用 OpenAlex API 查詢文獻。
    如果找到結果且標題相似度高，返回 OpenAlex 連結。
    """
    params = {
        'search': title,
        'per_page': 1,
        'select': 'title,doi,id' # 需要 title 和 id 進行比對和返回 URL
    }

    result = _call_external_api_with_retry(OPENALEX_API_URL, params, "OpenAlex")

    if result and result.get('results'):
        match = result['results'][0]
        oa_title = match.get('title')
        doi_url = match.get('doi')
        openalex_id = match.get('id')
        
        if oa_title and (doi_url or openalex_id):
            # [新增] 執行相似度檢查
            cleaned_query = clean_title(title)
            cleaned_oa = clean_title(oa_title)

            if not cleaned_query or not cleaned_oa:
                 return None

            # 精確匹配或相似度達門檻
            if cleaned_query == cleaned_oa or SequenceMatcher(None, cleaned_query, cleaned_oa).ratio() >= TITLE_SIMILARITY_THRESHOLD:
                return doi_url if doi_url else openalex_id # OpenAlex 優先回傳 DOI URL
        
    return None