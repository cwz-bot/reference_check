import streamlit as st
import pandas as pd
import time
import os
import re
import ast
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# ==============================================================================
# 1. 自動環境修復邏輯 (保留你原本成功的解決方案)
# ==============================================================================
def ensure_anystyle_installed():
    try:
        # 檢查 anystyle 指令是否可用
        subprocess.run(["ruby", "-S", "anystyle", "--version"], capture_output=True, check=True)
    except:
        with st.spinner("正在初始化 AnyStyle 環境（安裝 Ruby 套件）..."):
            # 使用 --user-install 避開權限問題
            os.system("gem install anystyle-cli --user-install")
            # 將 User gem path 加入環境變數
            user_gem_path = subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
            os.environ["PATH"] += os.pathsep + user_gem_path

ensure_anystyle_installed()

# ==============================================================================
# 2. 導入模組
# ==============================================================================
try:
    from modules.parsers import parse_references_with_anystyle
    from modules.local_db import load_csv_data, search_local_database
    from modules.api_clients import (
        get_scopus_key, get_serpapi_key, search_crossref_by_doi,
        search_crossref_by_text, search_scopus_by_title,
        search_scholar_by_title, search_scholar_by_ref_text,
        search_s2_by_title, search_openalex_by_title, check_url_availability
    )
except ImportError as e:
    st.error(f"❌ 導入模組失敗：{e}。請確保 modules 檔案夾完整。")

# ==============================================================================
# 3. 頁面設定與樣式
# ==============================================================================
st.set_page_config(page_title="學術引用檢查器 (終極整合版)", page_icon="📚", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: bold; text-align: center; color: #4F46E5; margin-bottom: 1rem; }
    .status-badge { padding: 4px 10px; border-radius: 12px; font-size: 0.85em; font-weight: bold; margin-right: 5px; }
    .ref-box { background-color: #f8f9fa; padding: 12px; border-radius: 8px; font-family: 'Courier New', monospace; font-size: 0.9em; border: 1px solid #e0e0e0; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">📚 學術引用檢查器 (系統整合增強版)</div>', unsafe_allow_html=True)

if "structured_references" not in st.session_state: st.session_state.structured_references = []
if "results" not in st.session_state: st.session_state.results = []

# ==============================================================================
# 4. 核心輔助工具：人名與資料補救
# ==============================================================================
def format_name_field(data):
    if not data: return None
    if isinstance(data, str) and not (data.startswith('[') or data.startswith('{')): return data
    try:
        if isinstance(data, str):
            try: data = ast.literal_eval(data)
            except: return data
        names_list = []
        items = data if isinstance(data, list) else [data]
        for item in items:
            if isinstance(item, dict):
                parts = [p for p in [item.get('family'), item.get('given')] if p]
                names_list.append(", ".join(parts))
            else:
                names_list.append(str(item))
        return "; ".join(names_list)
    except: return str(data)

def refine_parsed_data(parsed_item):
    """ 整合同學的進階清洗邏輯 """
    item = parsed_item.copy()
    raw_text = item.get('text', '').strip()

    # 基礎清洗
    for key in ['doi', 'url', 'title', 'date']:
        if item.get(key) and isinstance(item[key], str):
            item[key] = item[key].strip(' ,.;)]}>')

    # DOI 提取與 URL 修復
    url_val = item.get('url', '')
    if url_val:
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)', url_val)
        if doi_match:
            item['doi'] = doi_match.group(1).strip('.')

    # 標題補救邏輯
    title = item.get('title', '')
    if not title or len(title) < 10:
        # 嘗試從其他欄位撈取 (AnyStyle 常誤判標題為期刊或出版商)
        for backup_key in ['publisher', 'container-title', 'journal']:
            val = item.get(backup_key)
            if val and isinstance(val, str) and len(val) > 15:
                item['title'] = val.strip()
                break
    
    # 格式化人名
    if item.get('authors'): item['authors'] = format_name_field(item['authors'])
    return item

# ==============================================================================
# 5. 側邊欄設定
# ==============================================================================
with st.sidebar:
    st.header("⚙️ 系統設定")
    DEFAULT_CSV_PATH = "112ndltd.csv"
    local_df = None
    target_col = None
    if os.path.exists(DEFAULT_CSV_PATH):
        @st.cache_data
        def read_data_cached(file): return load_csv_data(file)
        local_df = read_data_cached(DEFAULT_CSV_PATH)
        if local_df is not None:
            st.success(f"✅ 本地庫載入: {len(local_df)} 筆")
            target_col = "論文名稱" if "論文名稱" in local_df.columns else local_df.columns[0]
    
    st.divider()
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    st.info(f"API 狀態:\n- Scopus: {'✅' if scopus_key else '❌'}\n- SerpAPI: {'✅' if serpapi_key else '❌'}")

# ==============================================================================
# 6. 主功能區
# ==============================================================================
tab1, tab2, tab3 = st.tabs(["📝 輸入解析", "🔍 驗證結果", "📊 統計報告"])

with tab1:
    st.subheader("貼上參考文獻列表")
    raw_input = st.text_area("在此貼上文獻內容...", height=300, placeholder="直接貼上論文末尾的 References 列表")
    
    if st.button("🚀 開始解析", type="primary"):
        if not raw_input.strip():
            st.warning("請先輸入文字")
        else:
            st.session_state.structured_references = []
            st.session_state.results = []
            with st.spinner("AnyStyle 引擎處理中..."):
                _, struct_list = parse_references_with_anystyle(raw_input)
            if struct_list:
                st.session_state.structured_references = struct_list
                st.success(f"✅ 解析成功！共 {len(struct_list)} 筆。")
            else:
                st.error("❌ AnyStyle 解析異常，請檢查 Ruby 環境。")

with tab2:
    if not st.session_state.structured_references:
        st.info("請先在第一頁解析文獻。")
    else:
        if st.button("🔍 開始全自動並行驗證", type="primary"):
            st.session_state.results = []
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            refs = st.session_state.structured_references
            total = len(refs)
            results_buffer = []

            def check_single_task(idx, raw_ref):
                ref = refine_parsed_data(raw_ref)
                title = ref.get('title', '')
                text = ref.get('text', '')
                doi = ref.get('doi')
                parsed_url = ref.get('url')
                first_author = ref['authors'].split(';')[0].split(',')[0].strip() if ref.get('authors') else ""

                res = {
                    "id": idx, "title": title if title else "解析失敗 (保底搜尋)", 
                    "text": text, "parsed": ref, "sources": {}, "found_at_step": None, 
                    "debug_logs": {}, "suggestion": None
                }

                # 搜尋序列
                # Step 0: Local DB
                if bool(re.search(r'[\u4e00-\u9fff]', title)) and local_df is not None and title:
                    match_row, score = search_local_database(local_df, target_col, title, threshold=0.85)
                    if match_row is not None:
                        res["sources"]["Local DB"] = "匹配成功"
                        res["found_at_step"] = "0. Local Database"
                        return res

                # Step 1: Crossref
                if doi:
                    _, url, _ = search_crossref_by_doi(doi, target_title=title)
                    if url:
                        res["sources"]["Crossref"] = url
                        res["found_at_step"] = "1. Crossref (DOI)"
                        return res
                
                url, _ = search_crossref_by_text(title if len(title)>10 else text[:100], first_author)
                if url:
                    res["sources"]["Crossref"] = url
                    res["found_at_step"] = "1. Crossref (Search)"
                    return res

                # Step 2: API 序列 (OpenAlex -> S2 -> Scopus -> Scholar)
                for func, name in [
                    (lambda: search_openalex_by_title(title, first_author), "OpenAlex"),
                    (lambda: search_s2_by_title(title, first_author), "Semantic Scholar"),
                    (lambda: search_scopus_by_title(title, scopus_key) if scopus_key else (None, None), "Scopus"),
                    (lambda: search_scholar_by_title(title, serpapi_key) if serpapi_key else (None, None), "Google Scholar")
                ]:
                    try:
                        u, s = func()
                        if u:
                            res["sources"][name] = u
                            res["found_at_step"] = f"2. {name}"
                            return res
                    except: pass

                # Step 3: 網站檢查
                if parsed_url and parsed_url.startswith('http'):
                    if check_url_availability(parsed_url):
                        res["sources"]["Direct Link"] = parsed_url
                        res["found_at_step"] = "3. Website / Direct URL"
                        return res

                # 補救機制：Scholar Ref Text 建議
                if serpapi_key:
                    url_r, _ = search_scholar_by_ref_text(text, serpapi_key, target_title=title)
                    if url_r: res["suggestion"] = url_r

                return res

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(check_single_task, i+1, r): i for i, r in enumerate(refs)}
                for i, future in enumerate(as_completed(futures)):
                    results_buffer.append(future.result())
                    progress_bar.progress((i + 1) / total)
                    status_text.text(f"已完成: {i+1}/{total}")

            st.session_state.results = sorted(results_buffer, key=lambda x: x['id'])
            st.rerun()

    # 展示與篩選邏輯
    if st.session_state.results:
        # (此處可加入你原本的篩選統計 Badge 程式碼，空間關係省略但邏輯相同)
        for res in st.session_state.results:
            p = res['parsed']
            with st.expander(f"{res['id']}. {p.get('title', '無標題')[:80]}..."):
                st.write(f"**狀態:** {res['found_at_step'] or '❌ 未找到'}")
                st.json(p) # 方便 Debug
                if res['sources']:
                    for s, link in res['sources'].items():
                        st.markdown(f"🔗 **[{s}]({link})**")
                if res['suggestion']:
                    st.warning(f"💡 建議查看相似文獻: [Google Scholar]({res['suggestion']})")

with tab3:
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        st.bar_chart(df['found_at_step'].fillna('Not Found').value_counts())
        st.download_button("📥 下載完整 CSV", df.to_csv(index=False).encode('utf-8-sig'), "report.csv")
    else:
        st.info("尚無數據")
