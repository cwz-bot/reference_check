# app.py

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
    search_scholar_by_ref_text,
    search_s2_by_title,
    search_openalex_by_title,
    check_url_availability
)

# ========== 頁面設定 ==========
st.set_page_config(page_title="學術引用檢查器 (Local DB + Docker)", page_icon="📚", layout="wide")

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

st.markdown('<div class="main-header">📚 學術引用檢查器 (混合雲地版)</div>', unsafe_allow_html=True)

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

# ========== [核心修改] 2. 資料清洗與拆分修正 (究極版) ==========
def refine_parsed_data(parsed_item):
    """
    修正 AnyStyle 解析結果，包含強力 DOI 提取與 RFC 標題救援。
    """
    item = parsed_item.copy()
    
    # 1. 基礎清理：移除所有欄位的尾部標點
    for key in ['doi', 'url', 'title', 'date']:
        if item.get(key) and isinstance(item[key], str):
            item[key] = item[key].strip(' ,.;)]}>')

    # 2. [DOI 強力救援] 
    # 掃描 URL 欄位，尋找是否隱藏了 DOI (格式: 10.xxxx/xxxx)
    url_val = item.get('url', '')
    if url_val:
        # Regex 解釋: 匹配 10. 開頭，接著4-9位數字，斜線，然後是任意字元
        doi_match = re.search(r'(10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+)', url_val)
        if doi_match:
            extracted_doi = doi_match.group(1).strip('.')
            item['doi'] = extracted_doi
            
            # 如果 URL 只是 DOI 的連結 (如 https://doi.org/10...)，則清空 URL
            # 這樣可以避免 Step 6 把它當作網站去檢查
            if 'doi.org' in url_val or url_val.replace('http://', '').startswith(extracted_doi):
                item['url'] = None
    
    # 3. 標題救援 (RFC 等特殊格式)
    title = item.get('title', '')
    # [新增] journal 欄位，因為有時候 AnyStyle 會把長字串塞在這裡
    garbage_fields = ['publisher', 'container-title', 'journal', 'date', 'location', 'note']
    candidate_text = ""

    # 如果標題是空的，或者標題看起來像是年份/編號 (太短)
    if not title or len(title) < 5:
        for field in garbage_fields:
            val = item.get(field)
            if val and isinstance(val, str) and len(val) > 10:
                # 特徵：包含年份括號 "2004)" 或 "RFC"
                if re.search(r'\d{4}.*?[)\]]\.?\s', val) or "RFC" in val:
                    candidate_text = val
                    break
        
        if candidate_text:
            # 策略 A: 針對 "日期). 標題" 的格式 (放寬 Regex: \s+ 改為 \s*)
            match_a = re.search(r'\d{4}.*?[)\]]\.?\s*(.*?)(?=\s*[\(\[]RFC|\s*[\(\[]Online|\s*Avail|\s*$)', candidate_text, re.IGNORECASE)
            
            if match_a:
                extracted_title = match_a.group(1).strip()
                if len(extracted_title) > 3: # 確保抓到的不是空字串
                    item['title'] = extracted_title
            
            # 策略 B: 針對 RFC 直接切割
            elif "RFC" in candidate_text:
                parts = candidate_text.split("RFC")
                potential_title = parts[0]
                potential_title = re.sub(r'[\(\[]$', '', potential_title).strip()
                potential_title = re.sub(r'^.*?\d{4}.*?[)\]]\.?\s*', '', potential_title).strip()
                if len(potential_title) > 5:
                    item['title'] = potential_title

    # 4. 版次/出版社分離
    if item.get('edition') and not item.get('publisher'):
        ed_text = item['edition']
        match = re.search(r'^([(\[]?.*?(?:ed\.|edition|edn)[)\]]?)\s*[:.,]?\s*(.+)$', ed_text, re.IGNORECASE)
        if match:
            item['edition'] = match.group(1).strip()       
            item['publisher'] = match.group(2).strip(' .,') 
    
    # 5. 格式化人名
    if item.get('authors'): item['authors'] = format_name_field(item['authors'])
    if item.get('editor'): item['editor'] = format_name_field(item['editor'])
    
    return item

# ========== 側邊欄 ==========
with st.sidebar:
    st.header("⚙️ 設定")
    st.subheader("📂 本地資料庫")
    DEFAULT_CSV_PATH = "112ndltd.csv"
    local_df = None
    target_col = None
    if os.path.exists(DEFAULT_CSV_PATH):
        @st.cache_data
        def read_data_cached(file): return load_csv_data(file)
        local_df = read_data_cached(DEFAULT_CSV_PATH)
        if local_df is not None:
            st.success(f"✅ 已載入: {len(local_df)} 筆")
            default_idx = 0
            if "論文名稱" in local_df.columns: default_idx = list(local_df.columns).index("論文名稱")
            target_col = st.selectbox("比對欄位:", options=local_df.columns, index=default_idx, disabled=True)
    else:
        st.error(f"❌ 錯誤：找不到 {DEFAULT_CSV_PATH}")
    
    st.divider()
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    st.info(f"Scopus API: {'✅ 已載入' if scopus_key else '❌ 未設定'}")
    st.info(f"SerpAPI: {'✅ 已載入' if serpapi_key else '❌ 未設定'}")
    
    check_crossref = True
    check_scopus = True
    check_openalex = True
    check_s2 = True
    check_scholar = True

# ========== 主邏輯 ==========
tab1, tab2, tab3 = st.tabs(["📝 輸入與解析", "🔍 驗證結果", "📊 統計報告"])

with tab1:
    st.subheader("貼上參考文獻列表")
    raw_input = st.text_area("在此貼上內容...", height=300)
    
    if st.button("🚀 使用 AnyStyle 解析", type="primary"):
        if not raw_input:
            st.warning("請先輸入文字")
        else:
            st.session_state.structured_references = []
            st.session_state.results = []
            with st.spinner("正在呼叫 Docker 容器進行解析..."):
                raw_list, struct_list = parse_references_with_anystyle(raw_input)
            if struct_list:
                st.session_state.structured_references = struct_list
                print(struct_list)
                st.success(f"✅ 解析成功！共 {len(struct_list)} 筆。")
            else:
                st.error("❌ AnyStyle 本機解析失敗，請確認 Ruby / anystyle-cli 是否正確安裝。")

with tab2:
    if not st.session_state.structured_references:
        st.info("請先在第一頁輸入並解析文獻。")
    else:
        if st.button("🔍 開始驗證所有文獻 (循序模式)", type="primary"):
            st.session_state.results = []
            progress = st.progress(0)
            status_text = st.empty()
            
            refs = st.session_state.structured_references
            total = len(refs)
            results_buffer = []

            def check_single_sequential(idx, raw_ref):
                # 1. 強力清洗與欄位修正 (DOI 搬家發生在這裡)
                ref = refine_parsed_data(raw_ref)
                
                title = ref.get('title', '')
                text = ref.get('text', '')
                doi = ref.get('doi')     # 已經從 URL 救回來了
                parsed_url = ref.get('url')
                
                # 提取第一作者 (用於輔助搜尋)
                first_author = ""
                if ref.get('authors'):
                    auth_raw = ref['authors'].split(';')[0].split(',')[0]
                    first_author = auth_raw[:20].strip()

                res = {
                    "id": idx, "title": title, "text": text, "parsed": ref,
                    "sources": {}, "found_at_step": None
                }

                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', title)) if title else False

                # Step 0: Local DB
                if has_chinese and local_df is not None and target_col and title:
                    match_row, score = search_local_database(local_df, target_col, title, threshold=0.85)
                    if match_row is not None:
                        res["sources"]["Local DB"] = "本地資料庫匹配成功"
                        res["found_at_step"] = "0. Local Database"
                        return res

                # Step 1: Crossref (DOI or Text)
                if check_crossref:
                    if doi:
                        _, url = search_crossref_by_doi(doi)
                        if url:
                            res["sources"]["Crossref"] = url
                            res["found_at_step"] = "1. Crossref (DOI)"
                            return res
                    # 無 DOI，嘗試文字搜尋
                    elif title and len(title) > 5:
                        url = search_crossref_by_text(title, first_author)
                        if url:
                            res["sources"]["Crossref"] = url
                            res["found_at_step"] = "1. Crossref (Text)"
                            return res

                # Step 2: Scopus
                if check_scopus and scopus_key and title:
                    url = search_scopus_by_title(title, scopus_key)
                    if url:
                        res["sources"]["Scopus"] = url
                        res["found_at_step"] = "2. Scopus"
                        return res 

                # Step 3: OpenAlex (Smart Fallback)
                if check_openalex and title:
                    url = search_openalex_by_title(title, first_author)
                    if url:
                        res["sources"]["OpenAlex"] = url
                        res["found_at_step"] = "3. OpenAlex"
                        return res 

                # Step 4: Semantic Scholar (Smart Fallback)
                if check_s2 and title:
                    url = search_s2_by_title(title, first_author)
                    if url:
                        res["sources"]["Semantic Scholar"] = url
                        res["found_at_step"] = "4. Semantic Scholar"
                        return res 

                # Step 5: Google Scholar
                if check_scholar and serpapi_key:
                    if title:
                        url, status = search_scholar_by_title(title, serpapi_key)
                        if status in ["match", "similar"]:
                            res["sources"]["Google Scholar"] = url
                            res["found_at_step"] = "5. Scholar (Title)"
                            return res 
                    
                    url_r, status_r = search_scholar_by_ref_text(text, serpapi_key)
                    if status_r != "no_result":
                        res["sources"]["Google Scholar (補救)"] = url_r
                        res["found_at_step"] = "5. Scholar (Text)"
                        return res 

                # Step 6: Website Check
                # [修正] 嚴格網站檢查：
                # 1. 必須是 http 開頭
                # 2. 不能包含 'doi.org' (因為那是論文連結)
                # 3. 不能包含 '10.xxxx/' (避免漏網的 DOI)
                if parsed_url and parsed_url.startswith('http'):
                    is_doi_link = 'doi.org' in parsed_url or re.search(r'10\.\d{4}/', parsed_url)
                    
                    if not is_doi_link:
                        is_valid = check_url_availability(parsed_url)
                        if is_valid:
                            res["sources"]["Direct Link"] = parsed_url
                            res["found_at_step"] = "6. Website / Direct URL"
                            return res
                        else:
                            res["sources"]["Direct Link (Dead)"] = parsed_url
                            res["found_at_step"] = "6. Website (Link Failed)" 
                            return res

                return res

            max_workers = min(5, total)
            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {executor.submit(check_single_sequential, i+1, r): i for i, r in enumerate(refs)}
                for i, future in enumerate(as_completed(futures)):
                    try:
                        data = future.result()
                        results_buffer.append(data)
                        progress.progress((i + 1) / total)
                        status_text.text(f"正在檢查: {i+1}/{total}")
                    except Exception as e:
                        st.error(f"Error on item {i}: {e}")

            st.session_state.results = sorted(results_buffer, key=lambda x: x['id'])
            status_text.success("✅ 驗證完成！")
            time.sleep(1)
            st.rerun()

        if st.session_state.results:
            st.divider()
            col1, col2 = st.columns([1, 3])
            with col1:
                filter_option = st.selectbox(
                    "📂 篩選顯示結果",
                    ["全部顯示", "✅ 資料庫驗證", "🌐 網站有效來源", "⚠️ 網站 (連線失敗)", "❌ 未找到結果"],
                    index=0
                )
            
            verified_db_count = sum(1 for r in st.session_state.results if r.get('found_at_step') and "Website" not in r.get('found_at_step'))
            valid_web_count = sum(1 for r in st.session_state.results if r.get('found_at_step') == "6. Website / Direct URL")
            failed_web_count = sum(1 for r in st.session_state.results if r.get('found_at_step') == "6. Website (Link Failed)")
            unverified_count = len(st.session_state.results) - (verified_db_count + valid_web_count + failed_web_count)
            
            with col2:
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
                    
                    if res['sources']:
                        st.write("**🔗 驗證來源連結：**")
                        for src, link in res['sources'].items():
                            if src == "Direct Link": st.markdown(f"- 🌐 **原始網站 (已測試可連線)**: [點擊前往]({link})")
                            elif src == "Direct Link (Dead)": st.markdown(f"- ⚠️ **原始網站 (連線逾時/失敗，請手動確認)**: [點擊前往]({link})")
                            elif link.startswith("http"): st.markdown(f"- **{src}**: [點擊開啟]({link})")
                            else: st.markdown(f"- **{src}**: {link}")
                    else:
                        st.warning("在所有啟用的資料庫中皆未找到匹配項。")

with tab3:
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        df['Source'] = df['found_at_step'].fillna('Not Found')
        total = len(df)
        verified_count = len(df[df['Source'] != 'Not Found'])
        col1, col2 = st.columns(2)
        col1.metric("總文獻數", total)
        col2.metric("已識別來源數 (含網站)", verified_count, f"{verified_count/total*100:.1f}%")
        st.subheader("驗證來源分佈")
        st.bar_chart(df['Source'].value_counts())
        
        st.subheader("詳細資料表")
        export_data = []
        for r in st.session_state.results:
            row = r['parsed'].copy()
            row['id'] = r['id']
            row['verified_source'] = r.get('found_at_step', 'Not Found')
            row['verified_url'] = list(r['sources'].values())[0] if r['sources'] else ''
            export_data.append(row)
        st.dataframe(pd.DataFrame(export_data), use_container_width=True)
        csv = pd.DataFrame(export_data).to_csv(index=False).encode('utf-8-sig')
        st.download_button("📥 下載完整報告 CSV", csv, "report.csv", "text/csv")
    else:
        st.info("尚無數據")