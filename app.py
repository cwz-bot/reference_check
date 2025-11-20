# app.py
import streamlit as st
import pandas as pd
from io import BytesIO
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- 導入模組 ---
from modules.file_processors import (
    extract_paragraphs_from_docx, 
    extract_paragraphs_from_pdf,
)
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
    st.session_state.references = [] 
if "results" not in st.session_state:
    st.session_state.results = []
if "processing" not in st.session_state:
    st.session_state.processing = False

# ========== 主標題 ==========
st.markdown('<div class="main-header">📚 學術引用檢查器</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">自動驗證您的論文參考文獻 | 支援 APA、IEEE 等多種格式</div>', unsafe_allow_html=True)

# ========== 側邊欄設定 ==========
with st.sidebar:
    st.header("⚙️ 設定")
    
    # --- 1. 使用者輸入 Gemini Key ---
    st.subheader("🔑 Gemini API 設定")
    user_gemini_key = st.text_input(
        "請輸入您的 Gemini API Key",
        type="password",
        help="請前往 Google AI Studio 申請免費金鑰",
        placeholder="AIzaSy..."
    )
    
    if not user_gemini_key:
        st.warning("⚠️ 請輸入 Key 以開始使用")
    else:
        st.success("✅ Key 已輸入")

    st.divider()
    
    # --- 2. 其他 API 狀態 (Scopus/SerpAPI 仍讀取後台) ---
    st.subheader("📡 其他資料庫狀態")
    scopus_status = "✅ 系統已內建" if st.secrets.get("scopus_api_key") else "❌ 未設定 (部分功能受限)"
    serpapi_status = "✅ 系統已內建" if st.secrets.get("serpapi_key") else "❌ 未設定 (部分功能受限)"
    st.text(f"Scopus: {scopus_status}")
    st.text(f"SerpAPI: {serpapi_status}")
    
    st.divider()

    # --- 3. 隱藏檢查選項，直接寫死預設值 (依序檢查) ---
    check_crossref = True
    check_scopus = True
    check_scholar = True
    check_s2 = True
    check_openalex = True
    
    # 隱藏的參數設定
    similarity_threshold = 0.9  # 固定相似度 0.9
    enable_remedial = True      # 固定開啟補救搜尋
    
    st.info("ℹ️ 系統將自動依序檢查各大資料庫，確保引用正確性。")

# ========== 主要內容區 ==========
tab1, tab2, tab3 = st.tabs(["📤 上傳文件", "🔍 檢查結果", "📊 統計報告"])

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
    
    # --- 處理文件按鈕邏輯 ---
    if uploaded_file:
        st.divider()

        if st.button("🚀 開始處理文件", type="primary", use_container_width=True):
            if not user_gemini_key:
                st.error("❌ 請先在左側邊欄輸入 Gemini API Key！")
                st.stop()

            with st.spinner("正在解析文件..."):
                file_bytes = uploaded_file.read()

                if uploaded_file.name.endswith(".docx"):
                    paragraphs = extract_paragraphs_from_docx(BytesIO(file_bytes))
                else:
                    paragraphs = extract_paragraphs_from_pdf(BytesIO(file_bytes))

                st.success(f"✅ 成功提取 {len(paragraphs)} 個段落")
            
            # --- 使用 Gemini 進行解析 ---
            try:
                model = get_gemini_model(user_gemini_key) 
                
                with st.spinner("正在呼叫 Gemini API 解析參考文獻... (可能需要一點時間)"):
                    final_refs_objects, debug_info = parse_document_with_gemini(model, paragraphs)

                if final_refs_objects:
                    st.success(f"✅ Gemini 成功識別 {len(final_refs_objects)} 條參考文獻")
                    
                    # 儲存 Gemini 返回的結構化資料
                    st.session_state.references = final_refs_objects 
                    st.session_state.results = [] 
                    
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

        # --- 單筆檢查函式 ---
        def check_single_reference_v3(idx, ref_object, check_opts, api_keys, similarity_threshold):
            """
            使用從 Gemini 預先提取的資料來執行 API 檢查，固定順序，找到即停止。
            """
            result = {
                "index": idx,
                "text": ref_object.get("text", "N/A"),
                "title": ref_object.get("title"),
                "authors": ref_object.get("authors"),
                "venue": ref_object.get("venue"),
                "year": ref_object.get("year"),
                "doi": ref_object.get("doi"),
                "url": ref_object.get("url"),
                "style": ref_object.get("style", "Other"),
                "citation_format": ref_object.get("citation_format", "Other"),
                "sources": {}
            }

            # [修改] 只針對明確標示為 "Website" 的項目進行人工查詢
            # 其他所有格式（包含 Standard, Book, Preprint...）將會繼續往下執行搜尋
            if result["style"] == "Website":
                result["sources"]["人工查詢"] = {"status": "⚠️ 非學術格式 (網站)", "url": None}
                return result

            # 固定順序搜尋
            found = False

            # 1️⃣ Crossref (DOI)
            if not found and result["doi"] and check_opts["crossref"]:
                title, url = search_crossref_by_doi(result["doi"])
                if url:
                    result["sources"]["Crossref"] = {"status": "✅ 找到", "url": url}
                    found = True

            # 2️⃣ Scopus (標題)
            if not found and result["title"] and check_opts["scopus"] and api_keys.get("scopus"):
                scopus_url = search_scopus_by_title(result["title"], api_keys["scopus"])
                if scopus_url:
                    result["sources"]["Scopus"] = {"status": "✅ 找到", "url": scopus_url}
                    found = True

            # 3️⃣ OpenAlex (標題)
            if not found and result["title"] and check_opts["openalex"]:
                oa_url = search_openalex_by_title(result["title"])
                if oa_url:
                    result["sources"]["OpenAlex"] = {"status": "✅ 找到", "url": oa_url}
                    found = True

            # 4️⃣ Semantic Scholar (標題)
            if not found and result["title"] and check_opts["s2"]:
                s2_url = search_s2_by_title(result["title"])
                if s2_url:
                    result["sources"]["Semantic Scholar"] = {"status": "✅ 找到", "url": s2_url}
                    found = True

            # 5️⃣ Google Scholar (標題)
            if not found and result["title"] and check_opts["scholar"] and api_keys.get("serpapi"):
                scholar_url, scholar_status = search_scholar_by_title(
                    result["title"], api_keys["serpapi"], similarity_threshold
                )
                status_map = {
                    "match": "✅ 完全匹配",
                    "similar": "⚠️ 相似匹配",
                    "no_result": "❌ 未找到",
                    "error": "❌ 查詢錯誤"
                }
                result["sources"]["Google Scholar"] = {
                    "status": status_map.get(scholar_status, "❌ 未知"),
                    "url": scholar_url
                }

            # 若以上皆未找到，並啟用補救搜尋
            found_sources = any("✅" in s.get("status", "") for s in result["sources"].values())
            if not found_sources and enable_remedial and api_keys.get("serpapi"):
                remedial_url, remedial_status = search_scholar_by_ref_text(result["text"], api_keys["serpapi"])
                if remedial_status == "remedial":
                    result["sources"]["Google Scholar (補救)"] = {
                        "status": "✅ 補救成功",
                        "url": remedial_url
                    }

            return result


        # --- 開始檢查按鈕 ---
        if st.button("🔍 開始檢查所有引用", type="primary", use_container_width=True):
            st.session_state.results = []
            st.session_state.processing = True

            progress_bar = st.progress(0)
            status_text = st.empty()

            try:
                scopus_key = get_scopus_key() if check_scopus else None
                serpapi_key = get_serpapi_key() if (check_scholar or enable_remedial) else None
            except Exception as e:
                scopus_key = None
                serpapi_key = None
                st.warning(f"⚠️ 部分 API Key 未設定，可能影響檢查結果：{e}")

            api_keys = {"scopus": scopus_key, "serpapi": serpapi_key}
            check_opts = {
                "crossref": check_crossref,
                "scopus": check_scopus,
                "scholar": check_scholar,
                "s2": check_s2,
                "openalex": check_openalex,
            }

            refs = st.session_state.references
            total = len(refs)
            results = []

            max_workers = min(10, total)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        check_single_reference_v3, 
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

        # --- 顯示結果 ---
        if st.session_state.results:
            st.divider()

            # 篩選器
            col1, col2, col3 = st.columns(3)
            with col1:
                filter_option = st.selectbox(
                    "篩選結果",
                    ["全部", "已驗證", "未驗證", "部分驗證"]
                )

            active_check_count = 5

            for result in st.session_state.results:
                verified_count = sum(1 for s in result["sources"].values() if "✅" in s["status"])
                
                if filter_option == "已驗證" and verified_count == 0:
                    continue
                elif filter_option == "未驗證" and verified_count > 0:
                    continue
                elif filter_option == "部分驗證" and (verified_count == 0 or verified_count == active_check_count):
                    continue

                with st.expander(f"📄 引用 {result['index']}", expanded=False):
                    st.markdown(f'<div class="ref-item">{result["text"]}</div>', unsafe_allow_html=True)

                    # 顯示詳細欄位
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**📰 標題**: {result.get('title', '❌ (Gemini 無法擷取)')}")
                        st.write(f"**👤 作者**: {result.get('authors', 'N/A')}")
                        st.write(f"**📰 期刊/會議**: {result.get('venue', 'N/A')}")
                        st.write(f"**📅 年份**: {result.get('year', 'N/A')}")
                    with col2:
                        st.write(f"**🏷️ 格式 (Gemini)**: {result.get('style', 'Other')}")
                        st.write(f"**🔖 引用格式**: {result.get('citation_format', 'Other')}")
                        st.write(f"**🔍 DOI**: {result.get('doi', '❌ (Gemini 無)')}")
                        status_text = "✅ 已找到" if verified_count > 0 else "❌ 未找到"
                        st.write(f"**驗證狀態**: {status_text}")

                    # 顯示 Gemini 提取的 URL
                    gemini_url = result.get("url")
                    if gemini_url:
                        st.write(f"**🔗 來源網址 (Gemini)**: {gemini_url}")

                    # 顯示各資料來源檢查結果
                    if result.get("sources"):
                        st.write("**🔗 資料來源檢查結果**:")
                        for source, info in result["sources"].items():
                            status_class = "badge-success" if "✅" in info.get("status", "") else "badge-warning"
                            link = f'[🔗 連結]({info.get("url")})' if info.get("url") else ""
                            st.markdown(
                                f'<span class="status-badge {status_class}">{source}: {info.get("status", "未知")}</span> {link}',
                                unsafe_allow_html=True
                            )

# ========== Tab 3: 統計報告 ==========
with tab3:
    st.header("📊 檢查統計報告")
    
    if not st.session_state.results:
        st.warning("⚠️ 請先完成引用檢查")
    else:
        active_check_count = 5

        # 總體統計
        total = len(st.session_state.results)
        
        verified_count = 0
        unverified_count = 0
        
        for r in st.session_state.results:
            if any("✅" in s.get("status", "") for s in r["sources"].values()):
                verified_count += 1
            else:
                unverified_count += 1
        
        # 顯示指標卡片
        col1, col2, col3 = st.columns(3)
        
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
                <h2>{verified_count}</h2>
                <p>成功驗證</p>
            </div>
            """, unsafe_allow_html=True)
        
        with col3:
            st.markdown(f"""
            <div class="warning-card">
                <h2>{unverified_count}</h2>
                <p>未驗證/需人工</p>
            </div>
            """, unsafe_allow_html=True)
        
        st.divider()
        
        # 圖表區
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📈 驗證狀態")
            chart_data = pd.DataFrame({
                "狀態": ["成功驗證", "未驗證"],
                "數量": [verified_count, unverified_count]
            })
            st.bar_chart(chart_data.set_index("狀態"))
        
        with col2:
            st.subheader("🎯 引用格式分布")
            style_counts = {}
            for r in st.session_state.results:
                style = r["style"]
                style_counts[style] = style_counts.get(style, 0) + 1
            
            style_df = pd.DataFrame({
                "格式": list(style_counts.keys()),
                "數量": list(style_counts.values())
            })
            st.bar_chart(style_df.set_index("格式"))
        
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
                "驗證狀態": "成功" if any("✅" in s.get("status", "") for s in r["sources"].values()) else "失敗"
            }
            for source, info in r["sources"].items():
                row[f"{source}_狀態"] = info["status"]
                row[f"{source}_連結"] = info.get("url")
            export_data.append(row)
        
        df = pd.DataFrame(export_data)
        
        # 轉換為 CSV
        csv = df.to_csv(index=False, encoding="utf-8-sig")
        
        st.download_button(
            label="📥 下載 CSV 報告",
            data=csv,
            file_name="reference_check_report.csv",
            mime="text/csv",
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