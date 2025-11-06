# app.py

import streamlit as st
import urllib.parse
import pandas as pd
from datetime import datetime
from io import StringIO
import re # re 還是可能需要，保留

# ========== 從 modules 導入所有功能 ==========
from modules.api_clients import (
    get_scopus_key, get_serpapi_key, search_crossref_by_doi,
    search_scopus_by_title, search_scholar_by_title,
    search_scholar_by_ref_text
)
from modules.file_processors import (
    extract_paragraphs_from_docx, extract_paragraphs_from_pdf,
    extract_reference_section_improved, extract_reference_section_from_bottom,
    detect_and_split_ieee, merge_references_by_heads
)
from modules.parsers import (
    detect_reference_style, find_apa_matches,
    find_apalike_matches, split_multiple_apa_in_paragraph,
    extract_doi,
    get_reference_keys, extract_in_text_citations # [!] 導入 get_reference_keys (複數)
)
from modules.ui_components import analyze_single_reference
# ==========================================


# ========== 讀取 API Keys ==========
SCOPUS_API_KEY = get_scopus_key()
SERPAPI_KEY = get_serpapi_key()


# ========== Streamlit UI ==========
st.set_page_config(page_title="Reference Checker", layout="centered")
if "start_query" not in st.session_state:
    st.session_state.start_query = False
if "query_results" not in st.session_state:
    st.session_state.query_results = None
st.title("📚 Reference Checker")

st.markdown("""
<div style="background-color: #fff9db; padding: 15px; border-left: 6px solid #f1c40f; border-radius: 6px;">
    <span style="font-size: 16px; font-weight: bold;">注意事項</span><br>
    <span style="font-size: 15px; color: #444;">
    為節省核對時間，本系統只查對有 DOI 碼的期刊論文。並未檢查期刊名稱、作者、卷期、頁碼，僅針對篇名進行核對。本系統僅提供初步篩選參考，比對後應進行人工核對，不得直接以本系統核對結果作為學術倫理判斷的依據。
    </span>
</div>
""", unsafe_allow_html=True)
st.markdown(" ")

# [!] 引用審核的勾選框
st.session_state.check_citations = st.checkbox("🔬 **(Beta) 執行內文與文末引用比對**", value=False)
if st.session_state.check_citations:
    st.info("啟用引用比對：將會嘗試解析內文引用 (如 [1] 或 (Author, YYYY)) 並與文末列表比對。此功能為 Beta 版，可能無法完美解析所有格式。")


uploaded_files = st.file_uploader("請上傳最多 10 個 Word 或 PDF 檔案", type=["docx", "pdf"], accept_multiple_files=True)
if uploaded_files and len(uploaded_files) > 10:
    st.error("❌ 上傳檔案超過 10 個，請刪除部分檔案後再試一次。")
    st.stop()

start_button = st.button("🚀 開始查詢")

if uploaded_files and start_button:
    st.subheader("📊 正在查詢中，請稍候...")

    all_results = []

    for uploaded_file in uploaded_files:
        file_ext = uploaded_file.name.split(".")[-1].lower()
        st.markdown(f"📄 處理檔案： {uploaded_file.name}")

        file_progress = st.progress(0.0)
        scholar_logs = []

        # 檔案解析
        if file_ext == "docx":
            paragraphs = extract_paragraphs_from_docx(uploaded_file)
        elif file_ext == "pdf":
            paragraphs = extract_paragraphs_from_pdf(uploaded_file)
        else:
            st.warning(f"⚠️ 檔案 {uploaded_file.name} 格式不支援，將略過。")
            continue

        # ========== 1. 擷取內文與參考文獻區段 ==========
        body_paragraphs, reference_paragraphs, matched_keyword, matched_method = extract_reference_section_improved(paragraphs)

        if not reference_paragraphs and not matched_keyword:
            # Fallback
            body_paragraphs, reference_paragraphs, matched_keyword = extract_reference_section_from_bottom(paragraphs)
            matched_method = "標準標題識別（底部）"

        if not reference_paragraphs and not matched_keyword:
            st.error(f"❌ 無法識別檔案 {uploaded_file.name} 的參考文獻區段，將標記於報告中。")
            file_results = {
                "filename": uploaded_file.name, "no_reference_section": True,
                "title_pairs": [], "crossref_doi_hits": {}, "scopus_hits": {},
                "scholar_hits": {}, "scholar_similar": {}, "scholar_remedial": {},
                "not_found": [], "report_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "listed_but_not_cited": [], "cited_but_not_listed": [] # [!]
            }
            all_results.append(file_results)
            continue

        # ========== 2. 合併參考文獻段落 ==========
        if file_ext == "pdf":
            ieee_refs = detect_and_split_ieee(reference_paragraphs)
            merged_references = ieee_refs if ieee_refs else merge_references_by_heads(reference_paragraphs)
        else:
            merged_references = merge_references_by_heads(reference_paragraphs)

        # 補丁
        if len(merged_references) >= 2:
            first_style = detect_reference_style(merged_references[0])
            if first_style == "Unknown":
                merged_references[0] = merged_references[0].strip() + " " + merged_references[1].strip()
                del merged_references[1]

        # ========== 3. (UI) 顯示合併後的人工檢查列表 ==========
        with st.expander("擷取到的參考文獻段落（供人工檢查）"):
            st.markdown(f"參考文獻段落偵測方式：**{matched_method}**")
            st.markdown(f"起始關鍵段落：**{matched_keyword}**")
            for i, para in enumerate(merged_references, 1):
                st.markdown(f"**{i}.** {para}")

        # ========== 4. (新功能) 引用審核 ==========
        cited_keys = set()
        # [!] MODIFIED: New aggregation logic
        all_listed_keys = set()
        ref_to_keys_map = {} # e.g., {"1. Gao...": ["num:1", "apa:gao:2023"]}
        
        if st.session_state.check_citations:
            # 4a. 擷取內文引用
            try:
                cited_keys = extract_in_text_citations(body_paragraphs)
            except Exception as e:
                st.warning(f"⚠️ 內文引用解析失敗：{e}")
                
            # 4b. 擷取文末索引鍵 (在下一個迴圈中)
            
        # ========== 5. (UI) 逐筆解析 & 查詢 ==========
        title_pairs = []
        crossref_doi_hits = {}
        scopus_hits = {}
        scholar_hits = {}
        scholar_similar = {}
        scholar_remedial = {}
        not_found = []

        with st.expander("逐筆參考文獻解析結果（合併後段落 + 標題 + DOI + 格式）"):
            ref_index = 1
            for para in merged_references:
                # [!] 執行引用審核的 B 部分：建立文末索引
                if st.session_state.check_citations:
                    try:
                        # [!] MODIFIED: 呼叫複數函式 get_reference_keys
                        ref_keys = get_reference_keys(para) 
                        if ref_keys:
                            # [!] MODIFIED: New aggregation logic
                            ref_to_keys_map[para] = ref_keys
                            all_listed_keys.update(ref_keys)
                    except Exception as e:
                        st.warning(f"⚠️ 文末索引鍵解析失敗：{e}")

                # 執行現有邏輯：解析標題
                apa_matches = find_apa_matches(para)
                apalike_matches = find_apalike_matches(para)
                total_valid_years = len(apa_matches) + len(apalike_matches)

                if total_valid_years >= 2:
                    sub_refs = split_multiple_apa_in_paragraph(para)
                    st.markdown(f"🔍 強制切分段落（原始段落含 {total_valid_years} 個年份）：")
                    for sub_ref in sub_refs:
                        result = analyze_single_reference(sub_ref, ref_index)
                        if result:
                            title_pairs.append(result)
                        ref_index += 1
                else:
                    result = analyze_single_reference(para, ref_index)
                    if result:
                        title_pairs.append(result)
                    ref_index += 1

        # ========== 6. 執行 API 查詢 ==========
        total_queries = len(title_pairs)
        for i, (ref, title) in enumerate(title_pairs, 1):
            doi = extract_doi(ref)
            if doi:
                title_from_doi, url = search_crossref_by_doi(doi)
                if title_from_doi:
                    crossref_doi_hits[ref] = url
                    if total_queries > 0:
                        file_progress.progress(i / total_queries)
                    continue

            url = search_scopus_by_title(title, SCOPUS_API_KEY)
            if url:
                scopus_hits[ref] = url
            else:
                gs_url, gs_type = search_scholar_by_title(title, SERPAPI_KEY)
                scholar_logs.append(f"Google Scholar 回傳類型：{gs_type} / 標題：{title}")
                if gs_type == "match":
                    scholar_hits[ref] = gs_url
                elif gs_type == "similar":
                    scholar_similar[ref] = gs_url
                elif gs_type == "error":
                    not_found.append(ref)
                else:
                    remedial_url, remedial_type = search_scholar_by_ref_text(ref, SERPAPI_KEY)
                    scholar_logs.append(f"Google Scholar 回傳類型：remedial_{remedial_type} / 標題：{title}")
                    if remedial_type == "remedial":
                        scholar_remedial[ref] = remedial_url
                    else:
                        not_found.append(ref)

            if total_queries > 0:
                file_progress.progress(i / total_queries)

        if scholar_logs:
            with st.expander("Google Scholar 查詢過程紀錄"):
                for line in scholar_logs:
                    st.text(line)

        # ========== 7. (新功能) 處理引用審核結果 ==========
        cited_but_not_listed = []
        listed_but_not_cited = []
        if st.session_state.check_citations:
            # [!] MODIFIED: New aggregation logic
            
            # 1. 內文引用，但文末未列出 (Missing)
            cited_but_not_listed = sorted(list(cited_keys - all_listed_keys))
            
            # 2. 文末列出，但內文未引用 (Unused)
            listed_but_not_cited_raw = []
            for ref_text, keys_for_this_ref in ref_to_keys_map.items():
                is_cited = False
                if not keys_for_this_ref:
                    is_cited = False # 如果文獻連 key 都沒有，視為未引用
                else:
                    for key in keys_for_this_ref:
                        if key in cited_keys:
                            is_cited = True # 只要有一個 key 匹配上，就視為已引用
                            break
                if not is_cited:
                    listed_but_not_cited_raw.append(ref_text)
            
            listed_but_not_cited = sorted(listed_but_not_cited_raw)


        # ========== 8. 儲存所有結果 ==========
        file_results = {
            "filename": uploaded_file.name,
            "no_reference_section": False, # [!]
            "title_pairs": title_pairs,
            "crossref_doi_hits": crossref_doi_hits,
            "scopus_hits": scopus_hits,
            "scholar_hits": scholar_hits,
            "scholar_similar": scholar_similar,
            "scholar_remedial": scholar_remedial,
            "not_found": not_found,
            "report_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "listed_but_not_cited": listed_but_not_cited, # [!]
            "cited_but_not_listed": cited_but_not_listed  # [!]
        }

        all_results.append(file_results)

    # 檔案處理完畢，儲存至 session
    st.session_state.query_results = all_results

# ... (if st.session_state.get("serpapi_error") ... 保持不變) ...
if st.session_state.get("serpapi_error"):
    st.warning(f"⚠️ Google Scholar 查詢時發生錯誤：{st.session_state['serpapi_error']}")


# ========== [修改] 結果顯示 UI ==========

if st.session_state.query_results:
        st.markdown("---")
        st.subheader("📊 查詢結果分類")
        
        report_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        
        for result in st.session_state.query_results:
            uploaded_filename = result.get("filename", "未知檔案")
            report_time = result.get("report_time", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
            st.markdown(f"📄 檔案名稱： {uploaded_filename}")

            if result.get("no_reference_section"):
                st.error("❌ 未找到參考文獻區段，無法進行分析。")
                continue

            # [!] 取得新舊所有結果
            not_found = result.get("not_found", [])
            crossref_doi_hits = result.get("crossref_doi_hits", {})
            scholar_similar = result.get("scholar_similar", {})
            scholar_remedial = result.get("scholar_remedial", {})
            scopus_hits = result.get("scopus_hits", {})
            scholar_hits = result.get("scholar_hits", {})
            listed_but_not_cited = result.get("listed_but_not_cited", [])
            cited_but_not_listed = result.get("cited_but_not_listed", [])
            
            matched_count = len(crossref_doi_hits) + len(scopus_hits) + len(scholar_hits) + len(scholar_remedial)
            
            # [!] 建立 TABS
            tab_list = [
                f"🟢 命中結果（{matched_count}）",
                f"🟡 Google Scholar 類似標題（{len(scholar_similar)}）",
                f"🔴 均查無結果（{len(not_found)}）"
            ]
            
            # [!] 動態加入新的 TAB
            if st.session_state.check_citations:
                inconsistency_count = len(listed_but_not_cited) + len(cited_but_not_listed)
                tab_list.append(f"⚠️ 引用不一致（{inconsistency_count}）")

            tabs = st.tabs(tab_list)
            
            # Tab 1: 命中結果
            with tabs[0]:
                if crossref_doi_hits:
                    with st.expander(f"\U0001F7E2 Crossref DOI 命中（{len(crossref_doi_hits)}）"):
                        for i, (title, url) in enumerate(crossref_doi_hits.items(), 1):
                            st.markdown(f"{i}. {title}  \n🔗 [DOI 連結]({url})", unsafe_allow_html=True)
                # ... (其他命中結果 scopus, scholar, remedial 保持不變) ...
                if scopus_hits:
                    with st.expander(f"\U0001F7E2 Scopus 標題命中（{len(scopus_hits)}）"):
                        for i, (title, url) in enumerate(scopus_hits.items(), 1):
                            st.markdown(f"{i}. {title}  \n🔗 [Scopus 連結]({url})", unsafe_allow_html=True)
                if scholar_hits:
                    with st.expander(f"\U0001F7E2 Google Scholar 標題命中（{len(scholar_hits)}）"):
                        for i, (title, url) in enumerate(scholar_hits.items(), 1):
                            st.markdown(f"{i}. {title}  \n🔗 [Scholar 連結]({url})", unsafe_allow_html=True)
                if scholar_remedial:
                    with st.expander(f"\U0001F7E2 Google Scholar 補救命中（{len(scholar_remedial)}）"):
                        for i, (title, url) in enumerate(scholar_remedial.items(), 1):
                            st.markdown(f"{i}. {title}  \n🔗 [Scholar 連結]({url})", unsafe_allow_html=True)
                if not (crossref_doi_hits or scopus_hits or scholar_hits or scholar_remedial):
                    st.info("沒有命中任何參考文獻。")

            # Tab 2: 類似標題
            with tabs[1]:
                if scholar_similar:
                    for i, (title, url) in enumerate(scholar_similar.items(), 1):
                        with st.expander(f"{i}. {title}"):
                            st.markdown(f"🔗 [Google Scholar 結果連結]({url})", unsafe_allow_html=True)
                            st.warning("⚠️ 此為相似標題，請人工確認是否為正確文獻。")
                else:
                    st.info("無標題相似但不一致的結果。")

            # Tab 3: 均查無結果
            with tabs[2]:
                if not_found:
                    for i, title in enumerate(not_found, 1):
                        scholar_url = f"https://scholar.google.com/scholar?q={urllib.parse.quote(title)}"
                        st.markdown(f"{i}. {title}  \n🔗 [Google Scholar 搜尋]({scholar_url})", unsafe_allow_html=True)
                    st.markdown("👉 請考慮手動搜尋 Google Scholar。")
                else:
                    st.success("所有標題皆成功查詢！")

            # [!] Tab 4: 引用不一致 (新功能)
            if st.session_state.check_citations:
                with tabs[3]:
                    st.info("此功能為 Beta 版。它透過解析作者、年份或數字編號來比對，可能無法抓到所有格式。")
                    
                    with st.expander(f"🔴 文末列出，但內文未引用 (Unused) ({len(listed_but_not_cited)})"):
                        if listed_but_not_cited:
                            for i, ref in enumerate(listed_but_not_cited, 1):
                                st.markdown(f"{i}. {ref}")
                        else:
                            st.success("所有文末參考文獻均在內文中被引用。")

                    with st.expander(f"🟠 內文引用，但文末未列出 (Missing) ({len(cited_but_not_listed)})"):
                        if cited_but_not_listed:
                            st.warning("以下索引鍵 (e.g., apa:author:year 或 num:1) 在內文被引用，但在文末列表中找不到。")
                            for i, key in enumerate(cited_but_not_listed, 1):
                                st.markdown(f"{i}. `{key}`")
                        else:
                            st.success("所有內文引用均對應到文末參考文獻。")


        # ... (下載結果的 CSV 邏輯保持不變) ...
        
        st.markdown("---")

        export_data = []
        for result in st.session_state.query_results:
            filename = result["filename"]
            has_any = False

            if result.get("no_reference_section"):
                export_data.append([filename, "", "查無結果：未解析出參考文獻段落", ""])
                continue
                
            if not result.get("title_pairs") and not result.get("listed_but_not_cited") and not result.get("cited_but_not_listed"):
                # [!] 修正為更精確的訊息
                if not result.get("title_pairs") and st.session_state.check_citations:
                    export_data.append([filename, "", "查無結果：已找到參考文獻區段，但未解析出任何文獻標題 (引用審核結果請見下方)", ""])
                elif not result.get("title_pairs"):
                     export_data.append([filename, "", "查無結果：已找到參考文獻區段，但未解析出任何文獻標題", ""])
                # else: 繼續執行

            # API 查詢結果
            for ref, title in result.get("title_pairs", []):
                if ref in result["crossref_doi_hits"]:
                    export_data.append([filename, ref, "Crossref 有 DOI 資訊", result["crossref_doi_hits"][ref]])
                elif ref in result["scopus_hits"]:
                    export_data.append([filename, ref, "標題命中（Scopus）", result["scopus_hits"][ref]])
                elif ref in result["scholar_hits"]:
                    export_data.append([filename, ref, "標題命中（Google Scholar）", result["scholar_hits"][ref]])
                elif ref in result["scholar_similar"]:
                    export_data.append([filename, ref, "Google Scholar 類似標題", result["scholar_similar"][ref]])
                elif ref in result.get("scholar_remedial", {}):
                    export_data.append([filename, ref, "Google Scholar 補救命中", result["scholar_remedial"][ref]])
                elif ref in result["not_found"]:
                    scholar_url = f"https://scholar.google.com/scholar?q={urllib.parse.quote(ref)}"
                    export_data.append([filename, ref, "查無結果", scholar_url])

            # [!] 引用審核結果
            for ref in result.get("listed_but_not_cited", []):
                export_data.append([filename, ref, "引用不一致 (文末列出但內文未引用)", ""])
            
            for key in result.get("cited_but_not_listed", []):
                export_data.append([filename, key, "引用不一致 (內文引用但文末未列出)", ""])


        # ... (CSV 標頭和下載按鈕 ... 保持不變) ...
        total_refs = sum(len(r.get("title_pairs", [])) for r in st.session_state.query_results) 
        matched_crossref = sum(len(r.get("crossref_doi_hits", {})) for r in st.session_state.query_results) 
        matched_scopus = sum(len(r.get("scopus_hits", {})) for r in st.session_state.query_results) 
        matched_scholar = sum(len(r.get("scholar_hits", {})) for r in st.session_state.query_results) 
        matched_remedial = sum(len(r.get("scholar_remedial", {})) for r in st.session_state.query_results)
        matched_similar = sum(len(r.get("scholar_similar", {})) for r in st.session_state.query_results)
        matched_notfound = sum(len(r.get("not_found", [])) for r in st.session_state.query_results) 

        # [!] 新增統計
        total_listed_not_cited = sum(len(r.get("listed_but_not_cited", [])) for r in st.session_state.query_results)
        total_cited_not_listed = sum(len(r.get("cited_but_not_listed", [])) for r in st.session_state.query_results)


        header = StringIO()
        header.write(f"報告產出時間：{report_time}\n\n")
        header.write("說明：\n")
        header.write("為節省核對時間，本系統只查對有DOI碼的期刊論文。且並未檢查期刊名稱、作者、卷期、頁碼。只針對篇名進行核對。\n")
        header.write("本系統只是為了提供初步篩選，比對後應接著進行人工核對，任何人都不應該以本系統核對結果作為任何學術倫CRI判斷之基礎。\n\n")

        csv_buffer = StringIO()
        csv_buffer.write(header.getvalue())
        if not export_data:
            df_export = pd.DataFrame([[
                "（無檔案）", "", "⚠️ 沒有可匯出的查核結果", ""
            ]], columns=["檔案名稱", "原始參考文獻", "查核結果", "連結"])
        else:
            # [!] 修正 CSV 標頭
            df_export = pd.DataFrame(export_data, columns=["檔案名稱", "原始參考文獻/索引鍵", "查核結果", "連結"])

        df_export.to_csv(csv_buffer, index=False)

        # 統計所有檔案的總數
        total_files = len(st.session_state.query_results)
        
        st.markdown(f"""
        📌 查核結果說明：本次共處理 **{total_files} 篇論文**，總共擷取 **{total_refs} 篇參考文獻**，其中：

        - {matched_crossref} 篇為「Crossref 有 DOI 資訊」
        - {matched_scopus} 篇為「標題命中（Scopus）」
        - {matched_scholar} 篇為「標題命中（Google Scholar）」
        - {matched_remedial} 篇為「Google Scholar 補救命中」
        - {matched_similar} 篇為「Google Scholar 類似標題」
        - {matched_notfound} 篇為「查無結果」
        """)
        
        if st.session_state.check_citations:
            st.markdown(f"""
            📌 引用審核 (Beta) 結果：
            - {total_listed_not_cited} 篇為「文末列出但內文未引用」
            - {total_cited_not_listed} 筆為「內文引用但文末未列出」
            """)

        st.markdown("---")
        
        st.subheader("📥 下載查詢結果")

        st.download_button(
            label="📤 下載結果 CSV 檔",
            data=csv_buffer.getvalue().encode('utf-8-sig'),
            file_name="reference_results.csv",
            mime="text/csv"
        )
        st.write("🔁 若要重新上傳檔案，請按下鍵盤上的 F5 或點擊瀏覽器重新整理按鈕")

