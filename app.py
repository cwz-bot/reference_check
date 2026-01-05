# app.py 完整融合版

import streamlit as st
import pandas as pd
import time
import os
import re
import ast 
from concurrent.futures import ThreadPoolExecutor, as_completed

# 導入自定義模組
from modules.parsers import parse_references_with_anystyle
# 導入本地資料庫模組
from modules.local_db import load_csv_data, search_local_database
# 導入 API 客戶端
from modules.api_clients import (
    get_scopus_key,
    get_serpapi_key,
    search_crossref_by_doi,
    search_crossref_by_text,
    search_scopus_by_title,
    search_scholar_by_title,
    search_scholar_by_ref_text, # 第二段新增
    search_s2_by_title,
    search_openalex_by_title,
    check_url_availability
)

# ========== 頁面設定 ==========
st.set_page_config(page_title="學術引用檢查器 (系統增強版)", page_icon="📚", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2rem; font-weight: bold; text-align: center; color: #4F46E5; margin-bottom: 1rem; }
    .status-badge { padding: 4px 8px; border-radius: 12px; font-size: 0.8em; font-weight: bold; margin-right: 5px; }
    .ref-box { background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-family: monospace; font-size: 0.9em; color: #333; border: 1px solid #ddd; }
    
    div[data-testid="stMarkdownContainer"] table {
        width: 100%;
        border-collapse: collapse;
        margin-bottom: 10px;
    }
    div[data-testid="stMarkdownContainer"] td {
        padding: 8px 5px;
        border-bottom: 1px solid #f0f0f0;
        font-size: 0.95em;
    }
    div[data-testid="stMarkdownContainer"] th {
        display: none; 
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">📚 學術引用檢查器 (解析補救與建議增強版)</div>', unsafe_allow_html=True)

# ========== Session State ==========
if "structured_references" not in st.session_state: st.session_state.structured_references = []
if "results" not in st.session_state: st.session_state.results = []

# ========== [輔助] 1. 人名格式化 ==========
def format_name_field(data):
    if not data: return None
    if isinstance(data, str) and not (data.startswith('[') or data.startswith('{')): return data
    try:
        if isinstance(data, str):
            try: data = ast.literal_eval(data)
            except: return data
        names_list = []
        if isinstance(data, dict): data = [data]
        elif not isinstance(data, list): return str(data)
        for item in data:
            if isinstance(item, dict):
                parts = []
                if item.get('family'): parts.append(item['family'])
                if item.get('given'): parts.append(item['given'])
                if parts: names_list.append(", ".join(parts))
            else:
                names_list.append(str(item))
        return "; ".join(names_list)
    except:
        return str(data)

# ========== [核心補救] 2. 資料清洗與標題提取修正 ==========
def refine_parsed_data(parsed_item):
    item = parsed_item.copy()
    raw_text = item.get('text', '').strip()

    # 基礎符號清洗
    for key in ['doi', 'url', 'title', 'date']:
        if item.get(key) and isinstance(item[key], str):
            item[key] = item[key].strip(' ,.;)]}>')

    # --- [核心補寫：處理 StyleTTS 2 / AIOS 等格式] ---
    title = item.get('title', '')
    
    if not title or len(title) < 10:
        # 模式 A: 針對 "縮寫: 完整標題"
        abbr_match = re.search(r'^([A-Z0-9\-\.\s]{2,12}:\s*.+?)(?=\s*[,\[]|\s*Available|\s*\(|\bhttps?://|\.|$)', raw_text)
        if abbr_match:
            item['title'] = abbr_match.group(1).strip()
        else:
            # 模式 B: AnyStyle 把標題誤判為出版商或期刊
            for backup_key in ['publisher', 'container-title', 'journal']:
                val = item.get(backup_key)
                if val and len(str(val)) > 15:
                    item['title'] = str(val).strip()
                    break

    # DOI 提取
    url_val = item.get('url', '')
    if url_val:
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)', url_val)
        if doi_match:
            item['doi'] = doi_match.group(1).strip('.')

    if item.get('authors'): item['authors'] = format_name_field(item['authors'])
    if item.get('editor'): item['editor'] = format_name_field(item['editor'])
    
    return item

# ========== 側邊欄 ==========
with st.sidebar:
    st.header("⚙️ 設定")
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

# ========== 主頁面 ==========
tab1, tab2, tab3 = st.tabs(["📝 輸入解析", "🔍 驗證結果", "📊 統計報告"])

with tab1:
    st.subheader("貼上參考文獻列表")
    raw_input = st.text_area("在此輸入文獻內容...", height=300, placeholder="例如: StyleTTS 2: Towards Human-Level Text-to-Speech...")
    
    if st.button("🚀 開始解析", type="primary"):
        if not raw_input:
            st.warning("請先輸入文字")
        else:
            st.session_state.structured_references = []
            st.session_state.results = []
            with st.spinner("AnyStyle 解析中..."):
                _, struct_list = parse_references_with_anystyle(raw_input)
            if struct_list:
                st.session_state.structured_references = struct_list
                st.success(f"✅ 解析成功！共 {len(struct_list)} 筆。")
            else:
                st.error("❌ AnyStyle 解析異常。")

with tab2:
    if not st.session_state.structured_references:
        st.info("請先在第一頁解析文獻。")
    else:
        if st.button("🔍 開始全自動驗證 (併發模式)", type="primary"):
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

                res = {
                    "id": idx, "title": title if title else "解析失敗 (使用保底搜索)", 
                    "text": text, "parsed": ref,
                    "sources": {}, "found_at_step": None, "debug_logs": {},
                    "suggestion": None # 融合建議連結
                }

                # Step 0: Local DB
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', search_query))
                if has_chinese and local_df is not None and title:
                    match_row, score = search_local_database(local_df, target_col, title, threshold=0.85)
                    if match_row is not None:
                        res["sources"]["Local DB"] = "匹配成功"
                        res["found_at_step"] = "0. Local Database"
                        return res

                # Step 1: Crossref (DOI & Text)
                if doi:
                    _, url, status = search_crossref_by_doi(doi, target_title=title if title else None)
                    if url:
                        res["sources"]["Crossref"] = url
                        res["found_at_step"] = "1. Crossref (DOI)"
                        return res
                
                url, status = search_crossref_by_text(search_query, first_author)
                if url:
                    res["sources"]["Crossref"] = url
                    res["found_at_step"] = "1. Crossref (Search)"
                    return res

                # Step 3: Scopus
                if scopus_key:
                    url, status = search_scopus_by_title(search_query, scopus_key)
                    if url:
                        res["sources"]["Scopus"] = url
                        res["found_at_step"] = "2. Scopus"
                        return res

                # Step 4: OpenAlex / S2 / Scholar (Title)
                for api_func, step_name in [
                    (lambda: search_openalex_by_title(search_query, first_author), "3. OpenAlex"),
                    (lambda: search_s2_by_title(search_query, first_author), "4. Semantic Scholar"),
                    (lambda: search_scholar_by_title(search_query, serpapi_key), "5. Google Scholar")
                ]:
                    try:
                        url, status = api_func()
                        if url:
                            res["sources"][step_name.split(". ")[1]] = url
                            res["found_at_step"] = step_name
                            return res
                        res["debug_logs"][step_name] = status
                    except: pass

                # 補救機制：Scholar Ref Text (存入 Suggestion)
                if serpapi_key:
                    url_r, status_r = search_scholar_by_ref_text(text, serpapi_key, target_title=title)
                    if url_r:
                        res["suggestion"] = url_r
                        res["debug_logs"]["Scholar (Suggestion)"] = "找到相似結果但未達驗證標準"
                    else:
                        res["debug_logs"]["Scholar (Text)"] = status_r

                # Step 5: Website URL Check
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

    # --- 結果展示區 ---
    if st.session_state.results:
        st.divider()
        
        # 融合第二段的進階篩選與統計
        verified_db_count = sum(1 for r in st.session_state.results if r.get('found_at_step') and "Website" not in r.get('found_at_step'))
        valid_web_count = sum(1 for r in st.session_state.results if r.get('found_at_step') == "6. Website / Direct URL")
        failed_web_count = sum(1 for r in st.session_state.results if r.get('found_at_step') == "6. Website (Link Failed)")
        unverified_count = len(st.session_state.results) - (verified_db_count + valid_web_count + failed_web_count)

        col_f1, col_f2 = st.columns([1, 2])
        with col_f1:
            filter_option = st.selectbox(
                "📂 篩選顯示結果",
                ["全部顯示", "✅ 資料庫驗證", "🌐 網站有效來源", "⚠️ 網站 (連線失敗)", "❌ 未找到結果"],
                index=0
            )
        with col_f2:
            st.markdown(f"""
            <div style="padding-top: 10px;">
                <span class="status-badge" style="background:#D1FAE5; color:#065F46;">📚 資料庫: {verified_db_count}</span>
                <span class="status-badge" style="background:#DBEAFE; color:#1E40AF;">🌐 有效網站: {valid_web_count}</span>
                <span class="status-badge" style="background:#FEF3C7; color:#92400E;">⚠️ 網站(Fail): {failed_web_count}</span>
                <span class="status-badge" style="background:#FEE2E2; color:#991B1B;">❌ 未找到: {unverified_count}</span>
            </div>
            """, unsafe_allow_html=True)

        st.divider()

        for res in st.session_state.results:
            found_step = res.get('found_at_step')
            is_db_verified = found_step and "Website" not in found_step
            is_web_valid = found_step == "6. Website / Direct URL"
            is_web_failed = found_step == "6. Website (Link Failed)"
            
            # 篩選邏輯融合
            if filter_option == "✅ 資料庫驗證" and not is_db_verified: continue
            if filter_option == "🌐 網站有效來源" and not is_web_valid: continue
            if filter_option == "⚠️ 網站 (連線失敗)" and not is_web_failed: continue
            if filter_option == "❌ 未找到結果" and (is_db_verified or is_web_valid or is_web_failed): continue

            bg_color = "#FEE2E2"
            if is_db_verified: bg_color = "#D1FAE5"
            elif is_web_valid: bg_color = "#DBEAFE"
            elif is_web_failed: bg_color = "#FEF3C7"
            
            status_label = f"✅ {found_step}" if is_db_verified else (f"🌐 {found_step}" if is_web_valid else (f"⚠️ {found_step}" if is_web_failed else "❌ 未找到"))
            
            p = res.get('parsed', {})
            with st.expander(f"{res['id']}. {p.get('title', '無標題')[:80]}..."):
                st.markdown(f"""<div style="background-color: {bg_color}; padding: 10px; border-radius: 5px; margin-bottom: 15px;"><b>狀態:</b> {status_label}</div>""", unsafe_allow_html=True)
                
                display_author = p.get('authors') or (f"{p['editor']} (Ed.)" if p.get('editor') else "N/A")
                display_title = p.get('title', 'N/A') + (f" {p['edition']}" if p.get('edition') else "")
                source_parts = [x for x in [p.get('container-title'), p.get('journal'), f"{p.get('location')}: {p.get('publisher')}" if p.get('publisher') else p.get('publisher')] if x]
                display_source = ", ".join(source_parts) if source_parts else "N/A"
                
                st.markdown(f"""
                | | |
                | :--- | :--- |
                | **👥 作者/編者** | `{display_author}` |
                | **📅 發表年份** | `{p.get('date', 'N/A')}` |
                | **📰 文獻標題** | `{display_title}` |
                | **🏢 出處/發行** | `{display_source}` |
                | **🔢 DOI/URL** | `{p.get('doi', p.get('url', 'N/A'))}` |
                """)
                
                st.divider()
                st.markdown("**📜 原始文獻:**")
                st.markdown(f"<div class='ref-box'>{res['text']}</div>", unsafe_allow_html=True)
                
                # 融合建議連結 (Suggestion)
                if res.get("suggestion"):
                    st.warning("💡 **輸入可能有誤，系統建議：**")
                    st.markdown(f"系統在模糊搜尋中找到了相似文獻，請確認：\n\n👉 **[點擊查看 Google Scholar 建議結果]({res['suggestion']})**")
                    st.caption("注意：此項未被正式標記為驗證成功。")
                    st.divider()
                
                if res['sources']:
                    st.write("**🔗 驗證來源連結：**")
                    for src, link in res['sources'].items():
                        if src == "Direct Link": st.markdown(f"- 🌐 **原始網站 (已測試)**: [點擊前往]({link})")
                        elif src == "Direct Link (Dead)": st.markdown(f"- ⚠️ **原始網站 (連線失敗)**: [點擊前往]({link})")
                        elif link.startswith("http"): st.markdown(f"- **{src}**: [點擊開啟]({link})")
                        else: st.markdown(f"- **{src}**: {link}")
                else:
                    st.error("⚠️ 資料庫皆未找到匹配項。")
                    with st.expander("🔍 查看 Debug Logs"):
                        for api, msg in res.get("debug_logs", {}).items():
                            st.write(f"**{api}**: {msg}")

with tab3:
    if st.session_state.results:
        df_res = pd.DataFrame(st.session_state.results)
        st.metric("總查核數", len(df_res))
        st.subheader("驗證來源分佈")
        st.bar_chart(df_res['found_at_step'].fillna('Not Found').value_counts())
        
        csv = df_res.to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報告 CSV", csv, "report.csv", "text/csv")
    else:
        st.info("尚無統計數據")