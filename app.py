# app.py 完整功能雲端版
import streamlit as st
import pandas as pd
import time
import os
import re
import ast 
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed

# 1. 雲端環境自動修復
# app.py 開頭的修復補丁

def ensure_anystyle_installed():
    # 1. 定義可能出現的路徑 (針對 Streamlit Cloud 的 Linux 環境)
    possible_paths = [
        "/home/appuser/.local/share/gem/ruby/3.1.0/bin",
        "/home/adminuser/.local/share/gem/ruby/3.1.0/bin",
        subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
    ]
    
    # 2. 將這些路徑加入系統環境變數 PATH
    for p in possible_paths:
        if p not in os.environ["PATH"]:
            os.environ["PATH"] = p + os.pathsep + os.environ["PATH"]

    # 3. 測試是否能執行
    try:
        subprocess.run(["anystyle", "--version"], capture_output=True, check=True)
    except:
        with st.spinner("☁️ 正在初始化雲端 AnyStyle 環境..."):
            # 如果還是找不到，嘗試再次安裝 (加上 --user-install 確保權限)
            os.system("gem install anystyle-cli --user-install")
            # 再次刷新路徑
            new_path = subprocess.getoutput("ruby -e 'print Gem.user_dir'") + "/bin"
            if new_path not in os.environ["PATH"]:
                os.environ["PATH"] = new_path + os.pathsep + os.environ["PATH"]

ensure_anystyle_installed()

# 2. 導入模組
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

# 3. 頁面設定與精美 UI
st.set_page_config(page_title="學術引用檢查器 (系統增強版)", page_icon="📚", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2.2rem; font-weight: bold; text-align: center; color: #4F46E5; margin-bottom: 1.5rem; }
    .status-badge { padding: 4px 10px; border-radius: 12px; font-size: 0.85em; font-weight: bold; margin-right: 5px; }
    .ref-box { background-color: #f8f9fa; padding: 12px; border-radius: 8px; font-family: monospace; font-size: 0.9em; color: #333; border: 1px solid #ddd; }
    div[data-testid="stMarkdownContainer"] table { width: 100%; border-collapse: collapse; margin-bottom: 10px; }
    div[data-testid="stMarkdownContainer"] td { padding: 8px 5px; border-bottom: 1px solid #f0f0f0; font-size: 0.95em; }
    div[data-testid="stMarkdownContainer"] th { display: none; }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">📚 學術引用檢查器 (全功能完整版)</div>', unsafe_allow_html=True)

# Session State
if "structured_references" not in st.session_state: st.session_state.structured_references = []
if "results" not in st.session_state: st.session_state.results = []

# 4. 輔助函式 (人名格式化與補救)
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
    
    # 確保所有基礎欄位都是字串，避免 re 報錯
    for key in ['doi', 'url', 'title', 'date']:
        val = item.get(key)
        if val and isinstance(val, str):
            item[key] = val.strip(' ,.;)]}>')
        elif val is not None:
            item[key] = str(val) # 強制轉字串

    # 標題補救邏輯
    title = item.get('title', '')
    if not title or len(title) < 10:
        abbr_match = re.search(r'^([A-Z0-9\-\.\s]{2,12}:\s*.+?)(?=\s*[,\[]|\s*Available|\s*\(|\bhttps?://|\.|$)', raw_text)
        if abbr_match:
            item['title'] = abbr_match.group(1).strip()
        else:
            for backup_key in ['publisher', 'container-title', 'journal']:
                val = item.get(backup_key)
                if val and len(str(val)) > 15:
                    item['title'] = str(val).strip()
                    break

    # DOI 提取補救 (安全性修正)
    current_url = item.get('url')
    if current_url and isinstance(current_url, str):
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)', current_url)
        if doi_match: 
            item['doi'] = doi_match.group(1).strip('.')

    if item.get('authors'): 
        item['authors'] = format_name_field(item['authors'])
    return item

# 5. 側邊欄
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
            st.success(f"✅ 已載入本地庫: {len(local_df)} 筆")
            target_col = "論文名稱" if "論文名稱" in local_df.columns else local_df.columns[0]
    
    st.divider()
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    st.info(f"Scopus API: {'✅' if scopus_key else '❌'}")
    st.info(f"SerpAPI: {'✅' if serpapi_key else '❌'}")

# 6. 主頁面
tab1, tab2, tab3 = st.tabs(["📝 輸入解析", "🔍 驗證結果", "📊 統計報告"])

with tab1:
    raw_input = st.text_area("在此輸入文獻內容...", height=300, placeholder="貼上 References...")
    if st.button("🚀 開始解析", type="primary"):
        if not raw_input: 
            st.warning("請先輸入文獻內容")
        else:
            # 清空舊結果
            st.session_state.structured_references = []
            st.session_state.results = []
            
            with st.spinner("正在解析文獻結構..."):
                # 這裡最關鍵：必須接收兩個值 (raw_texts, struct_list)
                raw_list, struct_list = parse_references_with_anystyle(raw_input)
                
                if struct_list:
                    st.session_state.structured_references = struct_list
                    st.success(f"✅ 解析完成，共 {len(struct_list)} 筆！請切換至「驗證結果」頁籤。")
                    # 強制頁面更新，這樣 Tab2 才會看到資料
                    time.sleep(1)
                    st.rerun() 
                else:
                    st.error("解析失敗，請確認 Log 或輸入格式。")

with tab2:
    if not st.session_state.structured_references:
        st.info("請先解析文獻。")
    else:
        if st.button("🔍 開始並行驗證", type="primary"):
            st.session_state.results = []
            progress = st.progress(0)
            status_text = st.empty()
            refs = st.session_state.structured_references
            total = len(refs)
            results_buffer = []

            def check_single_task(idx, raw_ref):
                ref = refine_parsed_data(raw_ref)
                title = ref.get('title', '')
                text = ref.get('text', '')
                search_query = title if (title and len(title) > 8) else text[:120]
                doi = ref.get('doi')
                parsed_url = ref.get('url')
                first_author = ref['authors'].split(';')[0].split(',')[0].strip() if ref.get('authors') else ""

                res = {"id": idx, "title": title, "text": text, "parsed": ref, "sources": {}, "found_at_step": None, "debug_logs": {}, "suggestion": None}

                # Step 0: Local DB
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', search_query))
                if has_chinese and local_df is not None and title:
                    match_row, score = search_local_database(local_df, target_col, title, threshold=0.85)
                    if match_row is not None:
                        res["sources"]["Local DB"] = "匹配成功"
                        res["found_at_step"] = "0. Local Database"
                        return res

                # Step 1: Crossref / Scopus ...
                if doi:
                    _, url, _ = search_crossref_by_doi(doi, target_title=title if title else None)
                    if url:
                        res["sources"]["Crossref"] = url
                        res["found_at_step"] = "1. Crossref (DOI)"
                        return res
                
                for api_func, step_name in [
                    (lambda: search_crossref_by_text(search_query, first_author), "1. Crossref"),
                    (lambda: search_scopus_by_title(search_query, scopus_key) if scopus_key else (None, "No Key"), "2. Scopus"),
                    (lambda: search_openalex_by_title(search_query, first_author), "3. OpenAlex"),
                    (lambda: search_s2_by_title(search_query, first_author), "4. Semantic Scholar"),
                    (lambda: search_scholar_by_title(search_query, serpapi_key) if serpapi_key else (None, "No Key"), "5. Google Scholar")
                ]:
                    try:
                        u, status = api_func()
                        if u:
                            res["sources"][step_name.split(". ")[1]] = u
                            res["found_at_step"] = step_name
                            return res
                        res["debug_logs"][step_name] = status
                    except: pass

                if serpapi_key:
                    url_r, _ = search_scholar_by_ref_text(text, serpapi_key, target_title=title)
                    if url_r: res["suggestion"] = url_r

                if parsed_url and parsed_url.startswith('http'):
                    if check_url_availability(parsed_url):
                        res["sources"]["Direct Link"] = parsed_url
                        res["found_at_step"] = "6. Website / Direct URL"
                    else:
                        res["sources"]["Direct Link (Dead)"] = parsed_url
                        res["found_at_step"] = "6. Website (Link Failed)"
                return res

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = {executor.submit(check_single_task, i+1, r): i for i, r in enumerate(refs)}
                for i, future in enumerate(as_completed(futures)):
                    results_buffer.append(future.result())
                    progress.progress((i + 1) / total)
                    status_text.text(f"正在檢查: {i+1}/{total}")
            st.session_state.results = sorted(results_buffer, key=lambda x: x['id'])
            st.rerun()

        # --- 完整結果展示 (修正縮進錯誤區) ---
        if st.session_state.results:
            # 1. 計算數據
            total_count = len(st.session_state.results)
            db_count = sum(1 for r in st.session_state.results if r.get('found_at_step') and "Website" not in r.get('found_at_step'))
            web_count = sum(1 for r in st.session_state.results if r.get('found_at_step') == "6. Website / Direct URL")
            fail_count = total_count - db_count - web_count

            # 2. 顯示統計
            st.markdown("### 📊 驗證即時統計")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("總文獻數", total_count)
            c2.metric("✅ 資料庫成功", db_count)
            c3.metric("🌐 網站來源", web_count)
            c4.metric("❌ 未找到", fail_count, delta="-"+str(fail_count) if fail_count > 0 else None)
            
            st.divider()
            filter_option = st.selectbox("📂 篩選顯示結果", ["全部顯示", "✅ 資料庫驗證", "🌐 網站有效來源", "❌ 未找到結果"])
            
            # 3. 循環顯示文獻結果 (此處縮進已對齊)
            for res in st.session_state.results:
                found_step = res.get('found_at_step')
                is_db = found_step and "Website" not in found_step
                is_web = found_step == "6. Website / Direct URL"
                is_fail = found_step == "6. Website (Link Failed)"

                if filter_option == "✅ 資料庫驗證" and not is_db: continue
                if filter_option == "🌐 網站有效來源" and not is_web: continue
                if filter_option == "❌ 未找到結果" and (is_db or is_web or is_fail): continue

                bg = "#D1FAE5" if is_db else ("#DBEAFE" if is_web else ("#FEF3C7" if is_fail else "#FEE2E2"))
                label = f"✅ {found_step}" if is_db else (f"🌐 {found_step}" if is_web else (f"⚠️ {found_step}" if is_fail else "❌ 未找到"))
                p = res.get('parsed', {})

                with st.expander(f"{res['id']}. {p.get('title', '無標題')[:80]}..."):
                    st.markdown(f'<div style="background:{bg}; padding:10px; border-radius:5px; margin-bottom:10px;"><b>狀態:</b> {label}</div>', unsafe_allow_html=True)
                    
                    st.markdown(f"""
                    | | |
                    | :--- | :--- |
                    | **👥 作者/編者** | `{p.get('authors', 'N/A')}` |
                    | **📅 發表年份** | `{p.get('date', 'N/A')}` |
                    | **📰 文獻標題** | `{p.get('title', 'N/A')}` |
                    | **🏢 出處/發行** | `{p.get('journal', p.get('publisher', 'N/A'))}` |
                    """)
                    
                    st.markdown("**📜 原始文獻:**")
                    st.markdown(f"<div class='ref-box'>{res['text']}</div>", unsafe_allow_html=True)
                    
                    if res.get("suggestion"):
                        st.warning(f"💡 [建議結果 (Google Scholar)]({res['suggestion']})")

                    if res['sources']:
                        st.write("**🔗 驗證連結：**")
                        for src, link in res['sources'].items():
                            st.write(f"- {src}: [{link}]({link})")
                    else:
                        with st.expander("🔍 查看 Debug Logs"):
                            for api, msg in res.get("debug_logs", {}).items():
                                st.write(f"**{api}**: {msg}")

with tab3:
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        st.bar_chart(df['found_at_step'].value_counts())
        st.download_button("📥 下載完整報告", df.to_csv(index=False).encode('utf-8-sig'), "report.csv")
    else:
        st.info("尚無數據")
