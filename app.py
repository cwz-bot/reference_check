# app.py
import streamlit as st
import pandas as pd
from io import BytesIO
import time

# --- [修改] 導入 ---
from modules.file_processors import (
    extract_paragraphs_from_docx, 
    extract_paragraphs_from_pdf,
)
# [新增] 導入 Gemini Client
from modules.gemini_client import get_gemini_model, parse_document_with_gemini

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
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 頁面設定 ==========
st.set_page_config(
    page_title="學術引用檢查器",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== 自訂 CSS ==========
st.markdown("""
<style>
    .main-header {
        font-size: 3rem;
        font-weight: bold;
        text-align: center;
        background: linear-gradient(90deg, #667eea 0%, #764ba2 100%);
        -webkit-background-clip: text;
        -webkit-text-fill-color: transparent;
        margin-bottom: 1rem;
    }
    .sub-header {
        text-align: center;
        color: #666;
        margin-bottom: 2rem;
    }
    .metric-card {
        background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
        text-align: center;
    }
    .success-card {
        background: linear-gradient(135deg, #11998e 0%, #38ef7d 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
    }
    .warning-card {
        background: linear-gradient(135deg, #f093fb 0%, #f5576c 100%);
        padding: 1.5rem;
        border-radius: 10px;
        color: white;
    }
    .ref-item {
        border-left: 4px solid #667eea;
        padding-left: 1rem;
        margin: 1rem 0;
        background: #f8f9fa;
        border-radius: 5px;
        padding: 1rem;
    }
    .status-badge {
        display: inline-block;
        padding: 0.25rem 0.75rem;
        border-radius: 15px;
        font-size: 0.85rem;
        font-weight: bold;
    }
    .badge-success { background: #38ef7d; color: white; }
    .badge-warning { background: #f5576c; color: white; }
    .badge-info { background: #667eea; color: white; }
</style>
""", unsafe_allow_html=True)

# ========== 初始化 Session State ==========
if "references" not in st.session_state:
    st.session_state.references = [] # 注意：現在儲存的是 [dict]
if "results" not in st.session_state:
    st.session_state.results = []
if "processing" not in st.session_state:
    st.session_state.processing = False
# [新增] 初始化 SerpAPI 錯誤狀態
if "serpapi_error" not in st.session_state:
    st.session_state.serpapi_error = None


# ========== 主標題 ==========
st.markdown('<div class="main-header">📚 學術引用檢查器</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">自動驗證您的論文參考文獻 | 支援 APA、IEEE 等多種格式</div>', unsafe_allow_html=True)

# ========== 側邊欄設定 ==========
with st.sidebar:
    st.header("⚙️ 設定")
    
    # --- API 設定區 (不變) ---
    st.subheader("🔑 API 金鑰")
    api_config = st.expander("API 設定", expanded=False)
    with api_config:
        # 檢查 API 金鑰狀態
        gemini_status = "✅ 已設定" if st.secrets.get("gemini_api_key") else "❌ 未設定 (必要)"
        scopus_status = "✅ 已設定" if st.secrets.get("scopus_api_key") else "❌ 未設定"
        serpapi_status = "✅ 已設定" if st.secrets.get("serpapi_key") else "❌ 未設定"
        st.write(f"Gemini API: {gemini_status}")
        st.write(f"Scopus API: {scopus_status}")
        st.write(f"SerpAPI: {serpapi_status}")
    
    st.divider()
    
    # 檢查選項：直接固定順序，不再讓使用者選擇
    st.subheader("🔍 檢查順序 (固定)")
    st.info("""
    **固定順序（找到即停止）：**
    1. Crossref (DOI)
    2. Scopus (標題)
    3. OpenAlex (標題)
    4. Semantic Scholar (標題)
    5. Google Scholar (標題)
    """)
    
    st.divider()
    
    # 進階設定 (移除標題相似度門檻)
    st.subheader("🎛️ 進階設定")
    
    # 標題相似度門檻：改為程式碼中固定值 0.90
    # similarity_threshold_fixed = 0.90
    
    enable_remedial = st.checkbox(
        "啟用補救搜尋",
        value=True,
        help="若標題檢查失敗，使用完整引用文字再次搜尋 (透過 Google Scholar)"
    )

# ========== 主要內容區 ==========
tab1, tab2, tab3 = st.tabs(["📤 上傳文件", "🔍 檢查結果", "📊 統計報告"])

# --- [修改] 單筆檢查函式 V2：加入順序和找到即停止邏輯 ---
def check_single_reference_v2(idx, ref_object, check_opts, api_keys, similarity_threshold):
    """
    使用從 Gemini 預先提取的資料來執行 API 檢查。
    **實行「優先順序查詢並找到即停止」的邏輯。**
    """
    result = {
        "index": idx,
        "text": ref_object.get("text", "N/A"),
        "title": ref_object.get("title"),
        "doi": ref_object.get("doi"),
        "style": ref_object.get("style", "Unknown"),
        "url": ref_object.get("url"),
        "sources": {}
    }
    
    # 判斷標題和 DOI 是否存在，是後續查詢的必要條件
    doi_exists = bool(result["doi"])
    title_exists = bool(result["title"])

    # 1. Crossref (DOI) - 優先級 1
    if doi_exists and check_opts["crossref"]:
        title, url = search_crossref_by_doi(result["doi"])
        if url:
            result["sources"]["Crossref"] = {"status": "✅ 找到", "url": url}
            return result # 找到即停止

    # 後續查詢需要標題
    if title_exists:
        # 2. Scopus - 優先級 2
        if check_opts["scopus"] and api_keys.get("scopus"):
            scopus_url = search_scopus_by_title(result["title"], api_keys["scopus"])
            if scopus_url:
                result["sources"]["Scopus"] = {"status": "✅ 找到", "url": scopus_url}
                return result # 找到即停止

        # 3. OpenAlex - 優先級 3
        if check_opts["openalex"]:
            oa_url = search_openalex_by_title(result["title"])
            if oa_url:
                result["sources"]["OpenAlex"] = {"status": "✅ 找到", "url": oa_url}
                return result # 找到即停止

        # 4. Semantic Scholar (S2) - 優先級 4
        if check_opts["s2"]:
            s2_url = search_s2_by_title(result["title"])
            if s2_url:
                result["sources"]["Semantic Scholar"] = {"status": "✅ 找到", "url": s2_url}
                return result # 找到即停止

        # 5. Google Scholar - 優先級 5 (作為最後的標題檢查)
        if check_opts["scholar"] and api_keys.get("serpapi"):
            scholar_url, scholar_status = search_scholar_by_title(
                result["title"], api_keys["serpapi"], similarity_threshold
            )
            
            status_map = {
                "match": "✅ 完全匹配",
                "similar": "⚠️ 相似匹配",
                "no_result": "❌ 未找到",
                "error": "❌ 查詢錯誤"
            }
            
            # 只有當狀態為匹配或相似時才停止
            if scholar_status in ["match", "similar"]:
                result["sources"]["Google Scholar"] = {
                    "status": status_map.get(scholar_status, "❌ 未知"),
                    "url": scholar_url
                }
                return result # 找到匹配或相似匹配即停止
            
            # 如果是 "no_result" 或 "error"，則記錄狀態，並繼續執行後續（補救）搜尋
            result["sources"]["Google Scholar"] = {
                "status": status_map.get(scholar_status, "❌ 未知"),
                "url": scholar_url
            }

    # [保留] 補救搜尋邏輯 (最後一道防線)
    # 只有當前面所有查詢（包括 Scholar 標題查詢）都沒有找到任何 "✅" 結果時，才執行
    found_sources = any("✅" in s.get("status", "") for s in result["sources"].values())
    
    if not found_sources and enable_remedial and api_keys.get("serpapi"):
        remedial_url, remedial_status = search_scholar_by_ref_text(
            result["text"], api_keys["serpapi"]
        )
        if remedial_status == "remedial":
            result["sources"]["Google Scholar (補救)"] = {
                "status": "✅ 補救成功",
                "url": remedial_url
            }

    return result

# ========== Tab 1: 上傳文件 ==========
with tab1:
    st.header("上傳您的論文文件")
    
    col1, col2 = st.columns([2, 1])
    
    with col1:
        uploaded_file = st.file_uploader(
            "支援格式：PDF、Word (.docx)",
            type=["pdf", "docx"],
            help="請上傳包含參考文獻區段的完整論文"
        )
    
    with col2:
        if uploaded_file:
            st.success("✅ 文件已上傳")
            st.info(f"📄 {uploaded_file.name}")
            st.write(f"大小: {uploaded_file.size / 1024:.1f} KB")
    
    # --- Tab 1: 處理文件按鈕邏輯 ---
    if uploaded_file:
        st.divider()
        
        if st.button("🚀 開始處理文件", type="primary", use_container_width=True):
            # [修正] 清空 SerpAPI 錯誤訊息，避免干擾
            st.session_state.serpapi_error = None
            
            with st.spinner("正在解析文件..."):
                if uploaded_file.name.endswith(".docx"):
                    paragraphs = extract_paragraphs_from_docx(uploaded_file)
                else:
                    paragraphs = extract_paragraphs_from_pdf(uploaded_file)
                
                st.success(f"✅ 成功提取 {len(paragraphs)} 個段落")
            
            # --- 使用 Gemini 進行解析 ---
            try:
                model = get_gemini_model() # 初始化 Gemini
                
                with st.spinner("正在呼叫 Gemini API 解析參考文獻... (可能需要一點時間)"):
                    final_refs_objects, debug_info = parse_document_with_gemini(model, paragraphs)

                if final_refs_objects:
                    st.success(f"✅ Gemini 成功識別 {len(final_refs_objects)} 條參考文獻")
                    
                    # 儲存 Gemini 返回的結構化資料
                    st.session_state.references = final_refs_objects 
                    st.session_state.results = [] # 清空舊結果
                    
                    st.subheader("📋 參考文獻預覽 (來自 Gemini)")
                    for i, ref_obj in enumerate(final_refs_objects[:3], 1):
                        with st.expander(f"引用 {i} (標題: {ref_obj.get('title', 'N/A')})"):
                            st.write(f"**原文:** {ref_obj.get('text')}")
                            st.info(f"**DOI:** {ref_obj.get('doi', 'N/A')} | **URL:** {ref_obj.get('url', 'N/A')} | **格式:** {ref_obj.get('style', 'N/A')}")
                    
                    if len(final_refs_objects) > 3:
                        st.info(f"...還有 {len(final_refs_objects) - 3} 條引用")
                
                else:
                    st.error(f"❌ Gemini 未能解析參考文獻。")
                    st.info(f"Gemini 回應: {debug_info}")

            except Exception as e:
                st.error(f"❌ 呼叫 Gemini API 失敗: {e}")
                st.stop()

# ========== Tab 2: 檢查結果 ==========
with tab2:
    st.header("引用驗證結果")
    
    if not st.session_state.references:
        st.warning("⚠️ 請先在「上傳文件」頁面處理文件")
    else:
        st.info(f"共有 {len(st.session_state.references)} 條參考文獻待檢查")

        # --- [修改] 開始檢查按鈕 ---
        if st.button("🔍 開始檢查所有引用", type="primary", use_container_width=True):
            st.session_state.results = []
            st.session_state.processing = True
            # [修正] 清空 SerpAPI 錯誤訊息，避免干擾
            st.session_state.serpapi_error = None

            progress_bar = st.progress(0)
            status_text = st.empty()

            try:
                # API 金鑰獲取邏輯保持不變
                scopus_key = get_scopus_key() # 總是嘗試獲取
                serpapi_key = get_serpapi_key() # 總是嘗試獲取
            except Exception as e:
                st.error(f"❌ API 金鑰設定錯誤：{e}")
                st.stop()

            api_keys = {"scopus": scopus_key, "serpapi": serpapi_key}
            
            # [修改] 由於選項被移除，所有檢查預設為 True
            check_opts = {
                "crossref": True,
                "scopus": True,
                "scholar": True,
                "s2": True,
                "openalex": True,
            }
            
            # [新增] 使用固定的相似度門檻
            similarity_threshold = 0.90 # 固定值
            
            refs = st.session_state.references
            total = len(refs)
            results = []

            max_workers = min(10, total)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        check_single_reference_v2, 
                        idx + 1, 
                        ref_object, 
                        check_opts, 
                        api_keys, 
                        similarity_threshold
                    ): idx
                    for idx, ref_object in enumerate(refs)
                }

                for i, future in enumerate(as_completed(futures), 1):
                    try:
                        result = future.result()
                        results.append(result)
                    except Exception as e:
                        st.error(f"❌ 第 {i} 條引用檢查失敗：{e}")
                        continue
                    progress_bar.progress(i / total)
                    status_text.text(f"完成 {i}/{total} 條引用")
            
            st.session_state.results = sorted(results, key=lambda r: r["index"])
            status_text.success("✅ 所有引用檢查完成！")
            st.session_state.processing = False
            time.sleep(1)
            st.rerun()

        # --- [修改] 顯示結果 ---
        if st.session_state.results:
            st.divider()

            # 篩選器
            col1, col2, col3 = st.columns(3)
            with col1:
                filter_option = st.selectbox(
                    "篩選結果",
                    ["全部", "已驗證", "未驗證", "部分驗證"]
                )

            # [FIX] 啟用的檢查總數，由於固定開啟，總數為 5
            active_check_count = 5 
            
            # [新增] 獲取 SerpAPI 錯誤訊息，如果存在的話
            serpapi_error = st.session_state.get("serpapi_error", None)

            for result in st.session_state.results:
                verified_count = sum(1 for s in result["sources"].values() if "✅" in s["status"])
                
                # 篩選邏輯
                if filter_option == "已驗證" and verified_count == 0:
                    continue
                elif filter_option == "未驗證" and verified_count > 0:
                    continue
                elif filter_option == "部分驗證" and (verified_count == 0 or verified_count == active_check_count):
                    continue


                with st.expander(f"📄 引用 {result['index']}", expanded=False):
                    st.markdown(f'<div class="ref-item">{result["text"]}</div>', unsafe_allow_html=True)

                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**📰 標題**: {result['title'] or '❌ (Gemini 無法擷取)'}")
                        st.write(f"**🏷️ 格式 (Gemini)**: {result['style']}")
                    with col2:
                        st.write(f"**🔍 DOI**: {result['doi'] or '❌ (Gemini 無)'}")
                        st.write(f"**✅ 驗證數**: {verified_count}/{active_check_count}")

                    gemini_url = result.get("url")
                    if gemini_url:
                        st.write(f"**🔗 來源網址 (Gemini)**: {gemini_url}")

                    if result["sources"]:
                        st.write("**🔗 資料來源檢查結果**:")
                        for source, info in result["sources"].items():
                            status_class = "badge-success" if "✅" in info["status"] else "badge-warning"
                            link = f'[🔗 連結]({info["url"]})' if info.get("url") else ""
                            
                            # === [修改/新增] 顯示 SerpAPI 錯誤詳情 ===
                            error_detail = ""
                            if source == "Google Scholar" and "錯誤" in info["status"] and serpapi_error:
                                # 使用 HTML 方式顯示錯誤詳情
                                error_detail = f'<p style="color: #f5576c; font-size: 0.85rem; margin-top: 5px; margin-bottom: 0px;">**SerpAPI 詳情:** {serpapi_error}</p>'
                            
                            st.markdown(
                                f'<span class="status-badge {status_class}">{source}: {info["status"]}</span> {link}{error_detail}',
                                unsafe_allow_html=True
                            )
                            # === [修改/新增] 結束 ===


# ========== Tab 3: 統計報告 ==========
with tab3:
    st.header("📊 檢查統計報告")
    
    if not st.session_state.results:
        st.warning("⚠️ 請先完成引用檢查")
    else:
        # [FIX] 重新計算 active_check_count (由於固定開啟，總數為 5)
        active_check_count = 5

        # 總體統計
        total = len(st.session_state.results)
        
        # 統計邏輯
        fully_verified = 0
        partially_verified = 0
        
        for r in st.session_state.results:
            verified_count = sum(1 for s in r["sources"].values() if "✅" in s["status"])
            
            if verified_count > 0:
                if verified_count == active_check_count:
                    fully_verified += 1
                else:
                    partially_verified += 1
        
        unverified = total - fully_verified - partially_verified
        
        # 顯示指標卡片
        col1, col2, col3, col4 = st.columns(4)
        
        with col1:
            st.markdown(f"""
            <div class="metric-card">
                <h2>{total}</h2>
                <p>總引用數</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col2:
            st.markdown(f"""
            <div class="success-card">
                <h2>{fully_verified}</h2>
                <p>完全驗證</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown(f"""
            <div class="warning-card">
                <h2>{partially_verified}</h2>
                <p>部分驗證</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col4:
            st.markdown(f"""
            <div class="warning-card">
                <h2>{unverified}</h2>
                <p>未驗證</p>
            </div>
            """, unsafe_allow_html=True)
        
        st.divider()
        
        # 圖表區
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📈 驗證狀態分布")
            chart_data = pd.DataFrame({
                "狀態": ["完全驗證", "部分驗證", "未驗證"],
                "數量": [fully_verified, partially_verified, unverified]
            })
            if not chart_data.empty:
                st.bar_chart(chart_data.set_index("狀態"))
        
        with col2:
            st.subheader("🎯 引用格式分布 (Gemini 偵測)")
            style_counts = {}
            for r in st.session_state.results:
                style = r["style"]
                style_counts[style] = style_counts.get(style, 0) + 1
            
            style_df = pd.DataFrame({
                "格式": list(style_counts.keys()),
                "數量": list(style_counts.values())
            })
            if not style_df.empty:
                st.bar_chart(style_df.set_index("格式"))
        
        st.divider()
        
        # 資料來源統計
        st.subheader("🔍 資料來源驗證統計")
        source_stats = {}
        for result in st.session_state.results:
            for source, info in result["sources"].items():
                if source not in source_stats:
                    source_stats[source] = {"成功": 0, "失敗/未查": 0}
                if "✅" in info["status"]:
                    source_stats[source]["成功"] += 1
                else:
                    # 只有在明確是失敗或未找到時才計入「失敗/未查」，排除補救搜尋
                    if "補救" not in source:
                        source_stats[source]["失敗/未查"] += 1
        
        if source_stats:
            source_df = pd.DataFrame(source_stats).T
            st.dataframe(source_df, use_container_width=True)
        
        st.divider()
        
        # 下載報告
        st.subheader("💾 匯出報告")
        
        # 準備 CSV 資料
        export_data = []
        for r in st.session_state.results:
            row = {
                "編號": r["index"],
                "引用文字": r["text"],
                "標題": r["title"],
                "DOI": r["doi"],
                "格式": r["style"],
                "來源網址": r.get("url"),
                "驗證來源數": sum(1 for s in r["sources"].values() if "✅" in s["status"])
            }
            for source, info in r["sources"].items():
                row[f"{source}_狀態"] = info["status"]
                row[f"{source}_連結"] = info.get("url")
            export_data.append(row)
        
        df = pd.DataFrame(export_data)
        
        # 轉換為 CSV
        csv = df.to_csv(index=False, encoding="utf-8-sig")
        
        col1, col2 = st.columns(2)
        with col1:
            st.download_button(
                label="📥 下載 CSV 報告",
                data=csv,
                file_name="reference_check_report.csv",
                mime="text/csv",
                use_container_width=True
            )
        
        with col2:
            # 生成摘要報告
            summary_list = ["# 學術引用檢查報告", "\n## 📊 總體統計"]
            if total > 0:
                summary_list.extend([
                    f"- 總引用數: {total}",
                    f"- 完全驗證: {fully_verified} ({fully_verified/total*100:.1f}%)",
                    f"- 部分驗證: {partially_verified} ({partially_verified/total*100:.1f}%)",
                    f"- 未驗證: {unverified} ({unverified/total*100:.1f}%)"
                ])
            
            summary_list.append("\n## 🎯 格式分布")
            summary_list.extend([f"- {k}: {v}" for k, v in style_counts.items()])
            
            summary_list.append("\n## 🔍 資料來源驗證率")
            for source, stats in source_stats.items():
                total_source_checks = stats['成功'] + stats['失敗/未查']
                if total_source_checks > 0:
                    summary_list.append(f"- {source}: {stats['成功']}/{total_source_checks} ({stats['成功']/total_source_checks*100:.1f}%)")
            
            summary = "\n".join(summary_list)
            
            st.download_button(
                label="📥 下載摘要報告",
                data=summary,
                file_name="reference_summary.md",
                mime="text/markdown",
                use_container_width=True
            )

# ========== 頁腳 ==========
st.divider()
st.markdown("""
<div style="text-align: center; color: #666; padding: 2rem;">
    <p>💡 提示：本工具由 Gemini API 驅動，自動解析引用</p>
    <p>🔒 您的文件僅在本次會話中處理，不會被儲存</p>
</div>
""", unsafe_allow_html=True)