# app.py (Cleaned Version)
import streamlit as st
import pandas as pd
import time
from io import BytesIO

# 核心功能模組
from modules.parsers import parse_references_with_anystyle # AnyStyle 解析器
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
# 移除 modules.ui_components 的 analyze_single_reference 導入 (該函式已停用)
from concurrent.futures import ThreadPoolExecutor, as_completed

# ========== 頁面設定 (不變) ==========
st.set_page_config(
    page_title="學術引用檢查器",
    page_icon="📚",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ========== 自訂 CSS (不變) ==========
st.markdown("""
<style>
    /* ... 保持您原來的 CSS ... */
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

# ========== 初始化 Session State (不變) ==========
if "references" not in st.session_state:
    st.session_state.references = []
if "structured_references" not in st.session_state: 
    st.session_state.structured_references = []
if "results" not in st.session_state:
    st.session_state.results = []
if "processing" not in st.session_state:
    st.session_state.processing = False

# ========== 主標題 (不變) ==========
st.markdown('<div class="main-header">📚 學術引用檢查器</div>', unsafe_allow_html=True)
st.markdown('<div class="sub-header">使用 AnyStyle 自動解析與驗證參考文獻</div>', unsafe_allow_html=True)

# ========== 側邊欄設定 (不變) ==========
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

# ========== 主要內容區 (Tab 1, 2, 3 邏輯不變) ==========
tab1, tab2, tab3 = st.tabs(["📝 輸入文獻", "🔍 檢查結果", "📊 統計報告"])

# ========== Tab 1: 輸入文獻 (不變) ==========
# ========== Tab 1: 輸入文獻 ==========
with tab1:
    st.header("貼上您的參考文獻")
    st.info("請將每條參考文獻貼在獨立的一行，或貼上整個參考文獻區塊。AnyStyle 將自動拆分和解析。")
    
    # 文本輸入框
    ref_text_input = st.text_area(
        "請在此處貼上參考文獻 (例如：[1] A. Einstein, \"On the electrodynamics of moving bodies,\" 1905)",
        height=300,
        key="raw_references_input"
    )
    
    # 處理按鈕: 使用唯一的 key
    parse_button_clicked = st.button(
        "🚀 開始解析參考文獻", 
        type="primary", 
        use_container_width=True,
        key="start_parsing_refs"  # 👈 修正：加入唯一 key
    )
    
    if parse_button_clicked:
        if not ref_text_input:
            st.warning("請先在文本框中貼上參考文獻。")
            # 停止執行後續的解析邏輯
            st.stop() 

        # 清空上一次的結果
        st.session_state.references = []
        st.session_state.structured_references = []
        st.session_state.results = []
        
        raw_text_for_anystyle = ref_text_input
        
        # 🌟 使用 AnyStyle 進行解析和拆分
        with st.spinner("🧠 正在使用 AnyStyle 解析參考文獻..."):
            final_refs_raw_list, final_refs_structured_list = parse_references_with_anystyle(raw_text_for_anystyle)
        
        if final_refs_structured_list:
            st.info(f"🤖 使用 AnyStyle 成功識別並解析文獻。")
            
            # 儲存結果
            st.session_state.references = final_refs_raw_list # 原始文本列表 (供顯示)
            st.session_state.structured_references = final_refs_structured_list # 結構化數據列表 (供檢查)
            st.success(f"✅ 成功識別 {len(final_refs_raw_list)} 條參考文獻")
            
            # 預覽前 3 條 (使用原始文本)
            st.subheader("📋 參考文獻預覽")
            for i, ref in enumerate(final_refs_raw_list[:3], 1):
                with st.expander(f"引用 {i}"):
                    st.write(ref)
            
            if len(final_refs_raw_list) > 3:
                st.info(f"...還有 {len(final_refs_raw_list) - 3} 條引用。請移至「檢查結果」頁面進行驗證。")
            
            st.session_state.active_tab = "🔍 檢查結果"
            
        else:
            st.error("❌ AnyStyle 解析參考文獻失敗，請檢查輸入內容或 AnyStyle 安裝。")


# ========== Tab 2: 檢查結果 (檢查邏輯不變) ==========
with tab2:
    st.header("引用驗證結果")
    
    if not st.session_state.structured_references:
        st.warning("⚠️ 請先在「輸入文獻」頁面貼上並解析文獻")
    else:
        # 引用檢查函式：使用 AnyStyle 結構化結果
        def check_single_reference(idx, ref_data, check_opts, api_keys, similarity_threshold):
            # 從 AnyStyle 結構化數據中提取所需的欄位
            ref_text = ref_data.get("text", "N/A")
            extracted_title = ref_data.get('title')
            extracted_doi = ref_data.get('doi')
            # 由於沒有自定義格式偵測，統一標籤
            style_label = ref_data.get('type', 'AnyStyle_Parsed') 

            result = {
                "index": idx,
                "text": ref_text,
                "title": extracted_title,
                "doi": extracted_doi,
                "style": style_label,
                "sources": {}
            }

            # ... (API 查詢邏輯保持不變) ...
            
            # Crossref (DOI)
            if result["doi"] and check_opts["crossref"]:
                title, url = search_crossref_by_doi(result["doi"])
                if url:
                    result["sources"]["Crossref"] = {"status": "✅ 找到", "url": url}
                    found = True

            # 其餘以標題搜尋
            if result["title"]:
                # Scopus
                if check_opts["scopus"] and api_keys.get("scopus"):
                    scopus_url = search_scopus_by_title(result["title"], api_keys["scopus"])
                    if scopus_url:
                        result["sources"]["Scopus"] = {"status": "✅ 找到", "url": scopus_url}

                # Google Scholar
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
                    result["sources"]["Google Scholar"] = {
                        "status": status_map.get(scholar_status, "❌ 未知"),
                        "url": scholar_url
                    }

                # Semantic Scholar
                if check_opts["s2"]:
                    s2_url = search_s2_by_title(result["title"])
                    if s2_url:
                        result["sources"]["Semantic Scholar"] = {"status": "✅ 找到", "url": s2_url}

                # OpenAlex
                if check_opts["openalex"]:
                    oa_url = search_openalex_by_title(result["title"])
                    if oa_url:
                        result["sources"]["OpenAlex"] = {"status": "✅ 找到", "url": oa_url}
            
            # 補救搜尋
            if enable_remedial and not any("✅" in s["status"] for s in result["sources"].values()):
                if check_opts["scholar"] and api_keys.get("serpapi"):
                     scholar_url, scholar_status = search_scholar_by_ref_text(
                        result["text"], api_keys["serpapi"]
                    )
                     if "match" in scholar_status or "similar" in scholar_status:
                         result["sources"]["Scholar (補救)"] = {"status": "✅ 補救找到", "url": scholar_url}

            return result

        # === 開始檢查按鈕 (邏輯不變) ===
        st.info(f"共有 {len(st.session_state.structured_references)} 條結構化文獻待檢查")

        if st.button("🔍 開始檢查所有引用", type="primary", use_container_width=True, key="start_verification"):
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
                "crossref": check_crossref, "scopus": check_scopus,
                "scholar": check_scholar, "s2": check_s2, "openalex": check_openalex,
            }

            refs_to_check = st.session_state.structured_references
            total = len(refs_to_check)
            results = []
            max_workers = min(10, total)

            with ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = {
                    executor.submit(
                        check_single_reference, idx + 1, ref_data, check_opts, api_keys, similarity_threshold
                    ): idx
                    for idx, ref_data in enumerate(refs_to_check)
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

        # === 顯示結果 (邏輯不變) ===
        if st.session_state.results:
            st.divider()
            col1, col2, col3 = st.columns(3)
            with col1:
                filter_option = st.selectbox(
                    "篩選結果",
                    ["全部", "已驗證", "未驗證", "部分驗證"]
                )

            active_check_count = 5

            for result in st.session_state.results:
                verified_count = sum(1 for s in result["sources"].values() if "✅" in s["status"])
                total_checks = len(result["sources"])

                if filter_option == "已驗證" and verified_count == 0: continue
                elif filter_option == "未驗證" and verified_count > 0: continue
                elif filter_option == "部分驗證" and (verified_count == 0 or verified_count == total_checks): continue

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
                            status_class = "badge-success" if "✅" in info["status"] else "badge-warning"
                            url_link = f'[🔗 連結]({info["url"]})' if info["url"] else '(無連結)'
                            st.markdown(
                                f'<span class="status-badge {status_class}">{source}: {info["status"]}</span> '
                                f'{url_link}',
                                unsafe_allow_html=True
                            )


# ========== Tab 3: 統計報告 (邏輯不變) ==========
with tab3:
    st.header("📊 檢查統計報告")
    
    if not st.session_state.results:
        st.warning("⚠️ 請先完成引用檢查")
    else:
        active_check_count = 5

        # 總體統計
        total = len(st.session_state.results)
        fully_verified = sum(1 for r in st.session_state.results if r["sources"] and all("✅" in s["status"] for s in r["sources"].values()))
        partially_verified = sum(1 for r in st.session_state.results if r["sources"] and any("✅" in s["status"] for s in r["sources"].values()) and not all("✅" in s["status"] for s in r["sources"].values()))
        unverified = total - fully_verified - partially_verified
        
        # 顯示指標卡片
        col1, col2, col3 = st.columns(3)
        
        with col1:
            st.markdown(f"""<div class="metric-card"><h2>{total}</h2><p>總引用數</p></div>""", unsafe_allow_html=True)
        with col2:
            st.markdown(f"""<div class="success-card"><h2>{fully_verified}</h2><p>完全驗證</p></div>""", unsafe_allow_html=True)
        with col3:
            st.markdown(f"""<div class="warning-card"><h2>{partially_verified}</h2><p>部分驗證</p></div>""", unsafe_allow_html=True)
        with col4:
            st.markdown(f"""<div class="warning-card"><h2>{unverified}</h2><p>未驗證</p></div>""", unsafe_allow_html=True)
        
        st.divider()
        
        # 圖表區
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("📈 驗證狀態分布")
            chart_data = pd.DataFrame({"狀態": ["完全驗證", "部分驗證", "未驗證"], "數量": [fully_verified, partially_verified, unverified]})
            st.bar_chart(chart_data.set_index("狀態"))
        
        with col2:
            st.subheader("🎯 引用格式分布")
            style_counts = {}
            for r in st.session_state.results:
                style = r["style"]
                style_counts[style] = style_counts.get(style, 0) + 1
            
            style_df = pd.DataFrame({"格式": list(style_counts.keys()), "數量": list(style_counts.values())})
            st.bar_chart(style_df.set_index("格式"))
        
        st.divider()
        
        # 資料來源統計
        st.subheader("🔍 資料來源驗證統計")
        source_stats = {}
        for result in st.session_state.results:
            for source, info in result["sources"].items():
                if source not in source_stats: source_stats[source] = {"成功": 0, "失敗": 0}
                if "✅" in info["status"]: source_stats[source]["成功"] += 1
                else: source_stats[source]["失敗"] += 1
        
        source_df = pd.DataFrame(source_stats).T
        st.dataframe(source_df, use_container_width=True)
        
        st.divider()
        
        # 下載報告
        st.subheader("💾 匯出報告")
        
        export_data = []
        for r in st.session_state.results:
            row = {"編號": r["index"], "引用文字": r["text"], "標題": r["title"], "DOI": r["doi"], "格式": r["style"], "驗證來源數": len([s for s in r["sources"].values() if "✅" in s["status"]])}
            for source, info in r["sources"].items():
                row[f"{source}_狀態"] = info["status"]
                row[f"{source}_連結"] = info.get("url")
            export_data.append(row)
        
        df = pd.DataFrame(export_data)
        
        csv_buffer = BytesIO()
        df.to_csv(csv_buffer, index=False, encoding="utf-8-sig")
        csv_bytes = csv_buffer.getvalue()

        col1, col2 = st.columns(2)
        with col1:
            st.download_button(label="📥 下載 CSV 報告", data=csv_bytes, file_name="reference_check_report.csv", mime="text/csv", use_container_width=True)
        
        with col2:
            def safe_div(n, d): return f"{n/d*100:.1f}" if d else "0.0"

            summary = f"""
# 學術引用檢查報告

## 📊 總體統計
- 總引用數: {total}
- 完全驗證: {fully_verified} ({safe_div(fully_verified, total)}%)
- 部分驗證: {partially_verified} ({safe_div(partially_verified, total)}%)
- 未驗證: {unverified} ({safe_div(unverified, total)}%)

## 🎯 格式分布
{chr(10).join(f"- {k}: {v}" for k, v in style_counts.items())}

## 🔍 資料來源驗證率
{chr(10).join(f"- {source}: {stats['成功']}/{stats['成功']+stats['失敗']} ({safe_div(stats['成功'], stats['成功']+stats['失敗'])}%)" for source, stats in source_stats.items() if stats['成功']+stats['失敗'] > 0)}
"""
            st.download_button(label="📥 下載摘要報告", data=summary, file_name="reference_summary.md", mime="text/markdown", use_container_width=True)

# ========== 頁腳 (不變) ==========
st.divider()
st.markdown("""
<div style="text-align: center; color: #666; padding: 2rem;">
    <p>💡 提示：本工具使用 AnyStyle (Ruby Gem) 進行高精度解析</p>
    <p>🔒 您的輸入不會被儲存</p>
</div>
""", unsafe_allow_html=True)