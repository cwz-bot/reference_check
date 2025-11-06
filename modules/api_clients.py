# modules/api_clients.py

import streamlit as st
import requests
import urllib.parse
from serpapi import GoogleSearch
from difflib import SequenceMatcher

# 從我們自己的模組導入
from .parsers import clean_title, clean_title_for_remedial

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

# ========== Crossref DOI 查詢 ==========
def search_crossref_by_doi(doi):
    url = f"https://api.crossref.org/works/{doi}"
    response = requests.get(url)
    if response.status_code == 200:
        item = response.json().get("message", {})
        titles = item.get("title")
        if isinstance(titles, list) and len(titles) > 0:
            return titles[0], item.get("URL")
        else:
            return None, item.get("URL")
    return None, None

# ========== Scopus 查詢 ==========
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

# ========== Serpapi 查詢 ==========
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