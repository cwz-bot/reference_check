# app.py 雲端穩定 + 一鍵報表版
import streamlit as st
import pandas as pd
import time
import os
import re
import ast 
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 1. 雲端環境自動修復 (保留原始補丁) ==========
def ensure_anystyle_installed():
    possible_paths = [
        "/home/appuser/.local/share/gem/ruby/3.1.0/bin",
        "/home/adminuser/.local/share/gem/ruby/3.1.0/bin",
        subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
    ]
    for p in possible_paths:
        if p not in os.environ["PATH"]:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

    try:
        subprocess.run(["anystyle", "--version"], capture_output=True, check=True)
    except:
        with st.spinner("☁️ 正在初始化雲端 AnyStyle 環境..."):
            os.system("gem install anystyle-cli --user-install")
            new_path = subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
            if new_path not in os.environ["PATH"]:
                os.environ["PATH"] = new_path + os.pathsep + os.environ["PATH"]

ensure_anystyle_installed()

# ========== 2. 導入模組 (保留原始 Try-Except) ==========
try:
    from modules.parsers import parse_references_with_anystyle
    from modules.local_db import load_csv_data, search_local_database
    from modules.api_clients import (
        get_scopus_key, get_serpapi_key, search_crossref_by_doi,
        search_crossref_by_text, search_scopus_by_title,
        search_scholar_by_title, search_scholar_by_ref_text,
        search_s2_by_title, search_openalex_by_title, check_url_availability
    )
except Exception as e:
    st.error(f"❌ 模組加載失敗: {e}")

# ========== 3. 頁面設定與 UI 樣式 ==========
st.set_page_config(page_title="學術引用檢查器 (報表增強版)", page_icon="📊", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: bold; text-align: center; color: #4F46E5; margin-bottom: 5px; }
    .sub-header { text-align: center; color: #6B7280; margin-bottom: 2rem; }
    .ref-box { background-color: #f8f9fa; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 0.9em; border: 1px solid #ddd; }
    .report-card { background-color: #FFFFFF; padding: 20px; border-radius: 10px; border: 1px solid #E5E7EB; box-shadow: 0 2px 4px rgba(0,0,0,0.05); }
</style>
""", unsafe_allow_html=True)

# Session State
if "results" not in st.session_state: st.session_state.results = []

# ========== 4. 輔助函式 (人名與數據清理) ==========
def format_name_field(data):
    if not data: return None
    try:
        if isinstance(data, str):
            if not (data.startswith('[') or data.startswith('{')): return data
            data = ast.literal_eval(data)
        names_list = []
        items = [data] if isinstance(data, dict) else data
        for item in items:
            if isinstance(item, dict):
                parts = [p for p in [item.get('family'), item.get('given')] if p]
                names_list.append(", ".join(parts))
            else: names_list.append(str(item))
        return "; ".join(names_list)
    except: return str(data)

def refine_parsed_data(parsed_item):
    item = parsed_item.copy()
    raw_text = item.get('text', '').strip()
    for key in ['doi', 'url', 'title', 'date']:
        val = item.get(key)
        if val and isinstance(val, str): item[key] = val.strip(' ,.;)]}>')
        elif val is not None: item[key] = str(val)

    title = item.get('title', '')
    if not title or len(title) < 10:
        abbr_match = re.search(r'^([A-Z0-9\-\.\s]{2,12}:\s*.+?)(?=\s*[,\[]|\s*Available|\s*\(|\bhttps?://|\.|$)', raw_text)
        if abbr_match: item['title'] = abbr_match.group(1).strip()
        else:
            for k in ['publisher', 'container-title', 'journal']:
                if item.get(k) and len(str(item[k])) > 15:
                    item['title'] = str(item[k]).strip()
                    break
    
    current_url = item.get('url')
    if current_url and isinstance(current_url, str):
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)', current_url)
        if doi_match: item['doi'] = doi_match.group(1).strip('.')

    if item.get('authors'): item['authors'] = format_name_field(item['authors'])
    return item

def check_single_task(idx, raw_ref, local_df, target_col, scopus_key, serpapi_key):
    ref = refine_parsed_data(raw_ref)
    title, text = ref.get('title', ''), ref.get('text', '')
    search_query = title if (title and len(title) > 8) else text[:120]
    doi, parsed_url = ref.get('doi'), ref.get('url')
    first_author = ref['authors'].split(';')[0].split(',')[0].strip() if ref.get('authors') else ""

    res = {"id": idx, "title": title, "text": text, "parsed": ref, "sources": {}, "found_at_step": None, "suggestion": None}

    # 0. Local DB
    if bool(re.search(r'[\u4e00-\u9fff]', search_query)) and local_df is not None and title:
        match_row, _ = search_local_database(local_df, target_col, title, threshold=0.85)
        if match_row is not None:
            res.update({"sources": {"Local DB": "匹配成功"}, "found_at_step": "0. Local Database"})
            return res

    # 1. APIs
    if doi:
        _, url, _ = search_crossref_by_doi(doi, target_title=title if title else None)
        if url: 
            res.update({"sources": {"Crossref": url}, "found_at_step": "1. Crossref (DOI)"})
            return res

    for api_func, step_name in [
        (lambda: search_crossref_by_text(search_query, first_author), "1. Crossref"),
        (lambda: search_scopus_by_title(search_query, scopus_key) if scopus_key else (None, None), "2. Scopus"),
        (lambda: search_openalex_by_title(search_query, first_author), "3. OpenAlex"),
        (lambda: search_s2_by_title(search_query, first_author), "4. Semantic Scholar"),
        (lambda: search_scholar_by_title(search_query, serpapi_key) if serpapi_key else (None, None), "5. Google Scholar")
    ]:
        try:
            url, _ = api_func()
            if url:
                res.update({"sources": {step_name.split(". ")[1]: url}, "found_at_step": step_name})
                return res
        except: pass

    if serpapi_key:
        url_r, _ = search_scholar_by_ref_text(text, serpapi_key, target_title=title)
        if url_r: res["suggestion"] = url_r

    if parsed_url and parsed_url.startswith('http'):
        if check_url_availability(parsed_url):
            res.update({"sources": {"Direct Link": parsed_url}, "found_at_step": "6. Website / Direct URL"})
        else:
            res.update({"sources": {"Direct Link (Dead)": parsed_url}, "found_at_step": "6. Website (Link Failed)"})
    return res

# ========== 5. 側邊欄設定 ==========
with st.sidebar:
    st.header("⚙️ 系統設定")
    DEFAULT_CSV_PATH = "112ndltd.csv"
    local_df, target_col = None, None
    if os.path.exists(DEFAULT_CSV_PATH):
        @st.cache_data
        def read_data_cached(file): return load_csv_data(file)
        local_df = read_data_cached(DEFAULT_CSV_PATH)
        if local_df is not None:
            st.success(f"✅ 已載入本地庫: {len(local_df)} 筆")
            target_col = "論文名稱" if "論文名稱" in local_df.columns else local_df.columns[0]
    
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    st.divider()
    st.caption("API 狀態確認:")
    st.write(f"Scopus: {'✅' if scopus_key else '❌'} | SerpAPI: {'✅' if serpapi_key else '❌'}")

# ========== 6. 主介面流程 (單頁一鍵版) ==========
st.markdown('<div class="main-header">📚 學術引用自動化查核報表</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">整合多方 API，一鍵產出引文驗證與 CSV 下載</div>', unsafe_allow_html=True)

# 輸入區
raw_input = st.text_area("請貼上參考文獻列表：", height=250, placeholder="例如：\nStyleTTS 2: Towards Human-Level Text-to-Speech...\nAIOS: LLM Agent Operating System...")

if st.button("🚀 開始全自動核對並生成報表", type="primary", use_container_width=True):
    if not raw_input:
        st.warning("⚠️ 請先貼上內容。")
    else:
        st.session_state.results = []
        with st.status("🔍 正在進行查核作業...", expanded=True) as status:
            status.write("正在解析引用格式...")
            _, struct_list = parse_references_with_anystyle(raw_input)
            
            if struct_list:
                status.write(f"正在連線各大學術資料庫 (共 {len(struct_list)} 筆)...")
                progress_bar = st.progress(0)
                results_buffer = []
                with ThreadPoolExecutor(max_workers=5) as executor:
                    futures = {executor.submit(check_single_task, i+1, r, local_df, target_col, scopus_key, serpapi_key): i for i, r in enumerate(struct_list)}
                    for i, future in enumerate(as_completed(futures)):
                        results_buffer.append(future.result())
                        progress_bar.progress((i + 1) / len(struct_list))
                
                st.session_state.results = sorted(results_buffer, key=lambda x: x['id'])
                status.update(label="✅ 核對作業完成！", state="complete", expanded=False)
            else:
                st.error("❌ AnyStyle 解析異常。")

# 結果顯示與下載
if st.session_state.results:
    st.divider()
    # 統計
    total_refs = len(st.session_state.results)
    verified_db = sum(1 for r in st.session_state.results if r.get('found_at_step') and "6." not in r.get('found_at_step'))
    failed_refs = total_refs - verified_db
    
    col1, col2, col3 = st.columns(3)
    col1.metric("總查核筆數", total_refs)
    col2.metric("資料庫匹配成功", verified_db)
    col3.metric("需人工確認/修正", failed_refs, delta_color="inverse")

    # 下載 CSV (UTF-8-SIG)
    df_export = pd.DataFrame([{
        "ID": r['id'],
        "狀態": r['found_at_step'] if r['found_at_step'] else "未找到",
        "抓取標題": r['title'],
        "原始內容": r['text'],
        "驗證連結": next(iter(r['sources'].values()), "N/A") if r['sources'] else "N/A"
    } for r in st.session_state.results])

    st.download_button(
        label="📥 下載完整查核報告 (Excel 可直接開啟)",
        data=df_export.to_csv(index=False).encode('utf-8-sig'),
        file_name=f"Report_{time.strftime('%m%d_%H%M')}.csv",
        mime="text/csv",
        use_container_width=True
    )

    # 異常項目清單
    st.markdown("---")
    st.markdown("#### ⚠️ 重點檢查清單 (未自動匹配項目)")
    error_items = [r for r in st.session_state.results if not r.get('found_at_step') or "Failed" in r.get('found_at_step')]
    
    if error_items:
        for item in error_items:
            with st.expander(f"❌ ID {item['id']}：{item['text'][:80]}..."):
                st.markdown(f"**原始內容：**")
                st.markdown(f"<div class='ref-box'>{item['text']}</div>", unsafe_allow_html=True)
                if item.get("suggestion"):
                    st.warning(f"💡 系統模糊搜尋結果：[請點此確認]({item['suggestion']})")
    else:
        st.success("🎉 所有引文均匹配成功！")

    with st.expander("🔍 查看所有驗證詳情"):
        st.write(pd.DataFrame(st.session_state.results)[['id', 'found_at_step', 'title']])
else:
    st.info("💡 尚未有結果，請在上方輸入文獻並點擊按鈕。")
