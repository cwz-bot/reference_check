# app.py

import streamlit as st
import pandas as pd
import time
import os
import re  # <--- [新增] 用於正規表達式判斷中文
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
    search_scopus_by_title,
    search_scholar_by_title,
    search_scholar_by_ref_text,
    search_s2_by_title,
    search_openalex_by_title
)

# ========== 頁面設定 ==========
st.set_page_config(page_title="學術引用檢查器 (Local DB + Docker)", page_icon="📚", layout="wide")

st.markdown("""
<style>
    .main-header { font-size: 2rem; font-weight: bold; text-align: center; color: #4F46E5; margin-bottom: 1rem; }
    .status-badge { padding: 4px 8px; border-radius: 12px; font-size: 0.8em; font-weight: bold; }
    .ref-box { background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-family: monospace; font-size: 0.9em; color: #333; border: 1px solid #ddd; }
    
    /* 表格樣式優化：隱藏表頭 */
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
        display: none; /* 隱藏表頭 */
    }
</style>
""", unsafe_allow_html=True)

st.markdown('<div class="main-header">📚 學術引用檢查器 (混合雲地版)</div>', unsafe_allow_html=True)

# ========== Session State ==========
if "structured_references" not in st.session_state: st.session_state.structured_references = []
if "results" not in st.session_state: st.session_state.results = []

# ========== 側邊欄 ==========
with st.sidebar:
    st.header("⚙️ 設定")
    
    # --- 1. 本地資料庫設定 (自動載入預設檔，隱藏上傳區) ---
    st.subheader("📂 本地資料庫 (優先檢查)")
    
    DEFAULT_CSV_PATH = "112ndltd.csv" # 鎖定預設檔案
    local_df = None
    target_col = None
    
    # 直接檢查檔案是否存在並載入
    if os.path.exists(DEFAULT_CSV_PATH):
        @st.cache_data
        def read_data_cached(file):
            return load_csv_data(file)

        local_df = read_data_cached(DEFAULT_CSV_PATH)
        
        if local_df is not None:
            st.success(f"✅ 已載入內建資料庫: {len(local_df)} 筆資料")
            
            # 自動偵測標題欄位
            default_idx = 0
            if "論文名稱" in local_df.columns:
                default_idx = list(local_df.columns).index("論文名稱")
            
            target_col = st.selectbox(
                "比對欄位:", # 簡化標籤
                options=local_df.columns,
                index=default_idx,
                disabled=True # 選項：您可以鎖定這個選單不讓人改，或者保留讓使用者看
            )
            st.info("💡 系統優先搜尋本地庫 (限中文文獻)，找不到才聯網。")
    else:
        st.error(f"❌ 錯誤：找不到預設檔案 {DEFAULT_CSV_PATH}")
        st.warning("請確認檔案已放入專案資料夾中。")
    
    st.divider()

    # --- 2. API 狀態 ---
    scopus_key = get_scopus_key()
    serpapi_key = get_serpapi_key()
    
    st.info(f"Scopus API: {'✅ 已載入' if scopus_key else '❌ 未設定'}")
    st.info(f"SerpAPI: {'✅ 已載入' if serpapi_key else '❌ 未設定'}")
    
    st.divider()
    
    # --- 3. 檢查順序說明 ---
    st.subheader("🔍 檢查順序")
    st.markdown("""
    系統將依序檢查直到找到結果：
    1. **本地 CSV 資料庫** (僅限中文)
    2. **Crossref** (DOI)
    3. **Scopus**
    4. **OpenAlex**
    5. **Semantic Scholar**
    6. **Google Scholar**
    """)
    
    # 強制開啟所有線上檢查
    check_crossref = True
    check_scopus = True
    check_openalex = True
    check_s2 = True
    check_scholar = True

# ========== 主邏輯 ==========
tab1, tab2, tab3 = st.tabs(["📝 輸入與解析", "🔍 驗證結果", "📊 統計報告"])

# --- TAB 1: 輸入 ---
with tab1:
    st.subheader("貼上參考文獻列表")
    st.info("💡 請直接貼上整段文獻，Docker 容器內的 AnyStyle 會自動識別並拆分。")
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
                st.success(f"✅ 解析成功！共識別出 {len(struct_list)} 筆文獻。請切換至「驗證結果」頁面。")
                with st.expander("預覽解析細節 (JSON)"):
                    st.json(struct_list[:3])
            else:
                st.error("解析失敗，請確認 Docker 是否正在執行。")

# --- TAB 2: 檢查 ---
with tab2:
    if not st.session_state.structured_references:
        st.info("請先在第一頁輸入並解析文獻。")
    else:
        # 開始檢查按鈕
        if st.button("🔍 開始驗證所有文獻 (循序模式)", type="primary"):
            st.session_state.results = []
            progress = st.progress(0)
            status_text = st.empty()
            
            refs = st.session_state.structured_references
            total = len(refs)
            results_buffer = []

            # 定義單筆檢查函式
            def check_single_sequential(idx, ref):
                title = ref.get('title', '')
                text = ref.get('text', '')
                doi = ref.get('doi')
                
                res = {
                    "id": idx,
                    "title": title,
                    "text": text,
                    "parsed": ref, # 保存解析資料
                    "sources": {},
                    "found_at_step": None
                }
                
                # --- [新增] 語言判斷邏輯 ---
                # 判斷標題是否包含中文字元 (Unicode 範圍 4E00-9FFF)
                # 如果沒有標題，則預設不含中文 (False)
                has_chinese = bool(re.search(r'[\u4e00-\u9fff]', title)) if title else False

                # 🛑 Step 0: 本地 CSV 資料庫 (最優先)
                # 只有當標題包含中文時，才搜尋本地資料庫
                if has_chinese and local_df is not None and target_col and title:
                    match_row, score = search_local_database(local_df, target_col, title, threshold=0.85)
                    if match_row is not None:
                        res["sources"]["Local DB"] = "本地資料庫匹配成功"
                        res["found_at_step"] = "0. Local Database"
                        return res

                # Step 1: Crossref
                if check_crossref and doi:
                    _, url = search_crossref_by_doi(doi)
                    if url:
                        res["sources"]["Crossref"] = url
                        res["found_at_step"] = "1. Crossref"
                        return res 

                # Step 2: Scopus
                if check_scopus and scopus_key and title:
                    url = search_scopus_by_title(title, scopus_key)
                    if url:
                        res["sources"]["Scopus"] = url
                        res["found_at_step"] = "2. Scopus"
                        return res 

                # Step 3: OpenAlex
                if check_openalex and title:
                    url = search_openalex_by_title(title)
                    if url:
                        res["sources"]["OpenAlex"] = url
                        res["found_at_step"] = "3. OpenAlex"
                        return res 

                # Step 4: Semantic Scholar
                if check_s2 and title:
                    url = search_s2_by_title(title)
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

                return res

            # 多執行緒執行
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

        # ======================================================
        # 顯示結果 (含篩選功能)
        # ======================================================
        if st.session_state.results:
            st.divider()
            
            # 篩選選單
            col1, col2 = st.columns([1, 3])
            with col1:
                filter_option = st.selectbox(
                    "📂 篩選顯示結果",
                    ["全部顯示", "✅ 已驗證成功", "❌ 未找到結果"],
                    index=0
                )
            
            # 統計數據
            verified_count = sum(1 for r in st.session_state.results if r.get('found_at_step'))
            unverified_count = len(st.session_state.results) - verified_count
            with col2:
                st.caption(f"總計: {len(st.session_state.results)} | ✅ 已驗證: {verified_count} | ❌ 未找到: {unverified_count}")

            st.divider()

            # 結果迴圈
            for res in st.session_state.results:
                found_step = res.get('found_at_step')
                is_verified = found_step is not None
                
                # --- 篩選邏輯 ---
                if filter_option == "✅ 已驗證成功" and not is_verified: continue
                if filter_option == "❌ 未找到結果" and is_verified: continue
                # ----------------

                status_label = f"✅ {found_step}" if found_step else "❌ 未找到"
                bg_color = "#D1FAE5" if found_step else "#FEE2E2"
                
                p = res.get('parsed', {})

                with st.expander(f"{res['id']}. {res['title'][:80]}..."):
                    # 1. 狀態列
                    st.markdown(f"""
                    <div style="background-color: {bg_color}; padding: 10px; border-radius: 5px; margin-bottom: 15px;">
                        <b>狀態:</b> {status_label}
                    </div>
                    """, unsafe_allow_html=True)
                    
                    # 2. 詳細欄位資料 (表格)
                    st.markdown(f"""
                    | | |
                    | :--- | :--- |
                    | **👥 作者** | `{p.get('authors', 'N/A')}` |
                    | **📅 年份** | `{p.get('date', 'N/A')}` |
                    | **📰 標題** | `{p.get('title', 'N/A')}` |
                    | **📖 期刊** | `{p.get('container-title', p.get('journal', 'N/A'))}` |
                    | **🔢 DOI** | `{p.get('doi', 'N/A')}` |
                    """)
                    
                    st.divider()

                    # 3. 原始文獻與連結
                    st.markdown("**📜 原始文獻:**")
                    st.markdown(f"<div class='ref-box'>{res['text']}</div>", unsafe_allow_html=True)
                    
                    if res['sources']:
                        st.write("**🔗 驗證來源連結：**")
                        for src, link in res['sources'].items():
                            if link.startswith("http"):
                                st.markdown(f"- **{src}**: [點擊開啟]({link})")
                            else:
                                st.markdown(f"- **{src}**: {link}")
                    else:
                        st.warning("在所有啟用的資料庫中皆未找到匹配項。")

# --- TAB 3: 統計 ---
with tab3:
    if st.session_state.results:
        df = pd.DataFrame(st.session_state.results)
        df['Source'] = df['found_at_step'].fillna('Not Found')
        
        total = len(df)
        verified_count = len(df[df['Source'] != 'Not Found'])
        
        col1, col2 = st.columns(2)
        col1.metric("總文獻數", total)
        col2.metric("成功驗證數", verified_count, f"{verified_count/total*100:.1f}%")
        
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