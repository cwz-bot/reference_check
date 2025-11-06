import streamlit as st
import pandas as pd
from io import BytesIO
import time

# 從模組導入功能
from modules.file_processors import (
    extract_paragraphs_from_docx, 
    extract_paragraphs_from_pdf,
    extract_reference_section_improved,
    detect_and_split_ieee,
    merge_references_by_heads
)
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
from modules.ui_components import analyze_single_reference
from modules.parsers import extract_title, extract_doi, detect_reference_style

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
    
    # API 設定區
    st.subheader("🔑 API 金鑰")
    api_config = st.expander("API 設定", expanded=False)
    with api_config:
        scopus_status = "✅ 已設定" if st.secrets.get("scopus_api_key") else "❌ 未設定"
        serpapi_status = "✅ 已設定" if st.secrets.get("serpapi_key") else "❌ 未設定"
        st.write(f"Scopus API: {scopus_status}")
        st.write(f"SerpAPI: {serpapi_status}")
    
    st.divider()
    
    # 檢查選項
    st.subheader("🔍 檢查選項")
    check_crossref = st.checkbox("Crossref (DOI)", value=True)
    check_scopus = st.checkbox("Scopus", value=True)
    check_scholar = st.checkbox("Google Scholar", value=True)
    check_s2 = st.checkbox("Semantic Scholar", value=True)
    check_openalex = st.checkbox("OpenAlex", value=True)
    
    st.divider()
    
    # 進階設定
    st.subheader("🎛️ 進階設定")
    similarity_threshold = st.slider(
        "標題相似度門檻",
        min_value=0.7,
        max_value=1.0,
        value=0.9,
        step=0.05,
        help="標題相似度需達此門檻才視為匹配"
    )
    
    enable_remedial = st.checkbox(
        "啟用補救搜尋",
        value=True,
        help="若標題檢查失敗，使用完整引用文字再次搜尋"
    )

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
    
    if uploaded_file:
        st.divider()
        
        # 處理文件按鈕
        if st.button("🚀 開始處理文件", type="primary", use_container_width=True):
            with st.spinner("正在解析文件..."):
                # 提取段落
                if uploaded_file.name.endswith(".docx"):
                    paragraphs = extract_paragraphs_from_docx(uploaded_file)
                else:
                    paragraphs = extract_paragraphs_from_pdf(uploaded_file)
                
                st.success(f"✅ 成功提取 {len(paragraphs)} 個段落")
                
                # 識別參考文獻區段
                body, refs_raw, matched_heading, method = extract_reference_section_improved(paragraphs)
                
                if refs_raw:
                    st.success(f"✅ 找到參考文獻區段！識別方法：{method}")
                    if matched_heading:
                        st.info(f"📌 識別到的標題：「{matched_heading}」")
                    
                    # 合併和處理引用
                    ieee_refs = detect_and_split_ieee(refs_raw)
                    if ieee_refs:
                        final_refs = ieee_refs
                        st.info("🔢 偵測到 IEEE 格式，已自動拆分")
                    else:
                        final_refs = merge_references_by_heads(refs_raw)
                    
                    st.session_state.references = final_refs
                    st.success(f"✅ 成功識別 {len(final_refs)} 條參考文獻")
                    
                    # 預覽前 3 條
                    st.subheader("📋 參考文獻預覽")
                    for i, ref in enumerate(final_refs[:3], 1):
                        with st.expander(f"引用 {i}"):
                            st.write(ref)
                    
                    if len(final_refs) > 3:
                        st.info(f"...還有 {len(final_refs) - 3} 條引用")
                    
                else:
                    st.error("❌ 未找到參考文獻區段，請檢查文件格式")

# ========== Tab 2: 檢查結果 ==========
with tab2:
    st.header("引用驗證結果")
    
    if not st.session_state.references:
        st.warning("⚠️ 請先在「上傳文件」頁面處理文件")
    else:
        st.info(f"共有 {len(st.session_state.references)} 條參考文獻待檢查")
        
        # 開始檢查按鈕
        if st.button("🔍 開始檢查所有引用", type="primary", use_container_width=True):
            st.session_state.results = []
            st.session_state.processing = True
            
            # 進度條
            progress_bar = st.progress(0)
            status_text = st.empty()
            
            # 取得 API 金鑰
            try:
                scopus_key = get_scopus_key() if check_scopus else None
                serpapi_key = get_serpapi_key() if check_scholar else None
            except:
                st.error("❌ API 金鑰設定錯誤，請檢查設定")
                st.stop()
            
            # 逐條檢查
            for idx, ref_text in enumerate(st.session_state.references, 1):
                status_text.text(f"正在檢查第 {idx}/{len(st.session_state.references)} 條引用...")
                
                result = {
                    "index": idx,
                    "text": ref_text,
                    "title": None,
                    "doi": None,
                    "style": None,
                    "sources": {}
                }
                
                # 提取基本資訊
                result["style"] = detect_reference_style(ref_text)
                result["title"] = extract_title(ref_text, result["style"])
                result["doi"] = extract_doi(ref_text)
                
                # 檢查各個來源
                if result["doi"] and check_crossref:
                    title, url = search_crossref_by_doi(result["doi"])
                    if url:
                        result["sources"]["Crossref"] = {"status": "✅ 找到", "url": url}
                
                if result["title"]:
                    if check_scopus and scopus_key:
                        scopus_url = search_scopus_by_title(result["title"], scopus_key)
                        if scopus_url:
                            result["sources"]["Scopus"] = {"status": "✅ 找到", "url": scopus_url}
                    
                    if check_scholar and serpapi_key:
                        scholar_url, scholar_status = search_scholar_by_title(
                            result["title"], serpapi_key, similarity_threshold
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
                    
                    if check_s2:
                        s2_url = search_s2_by_title(result["title"])
                        if s2_url:
                            result["sources"]["Semantic Scholar"] = {"status": "✅ 找到", "url": s2_url}
                    
                    if check_openalex:
                        oa_url = search_openalex_by_title(result["title"])
                        if oa_url:
                            result["sources"]["OpenAlex"] = {"status": "✅ 找到", "url": oa_url}
                
                st.session_state.results.append(result)
                progress_bar.progress(idx / len(st.session_state.references))
            
            status_text.success("✅ 檢查完成！")
            st.session_state.processing = False
            time.sleep(1)
            st.rerun()
        
        # 顯示結果
        if st.session_state.results:
            st.divider()
            
            # 篩選器
            col1, col2, col3 = st.columns(3)
            with col1:
                filter_option = st.selectbox(
                    "篩選結果",
                    ["全部", "已驗證", "未驗證", "部分驗證"]
                )
            
            # 顯示每條結果
            for result in st.session_state.results:
                verified_count = sum(1 for s in result["sources"].values() if "✅" in s["status"])
                total_checks = len(result["sources"])
                
                # 根據篩選器判斷是否顯示
                if filter_option == "已驗證" and verified_count == 0:
                    continue
                elif filter_option == "未驗證" and verified_count > 0:
                    continue
                elif filter_option == "部分驗證" and (verified_count == 0 or verified_count == total_checks):
                    continue
                
                with st.expander(f"📄 引用 {result['index']}", expanded=False):
                    st.markdown(f'<div class="ref-item">{result["text"]}</div>', unsafe_allow_html=True)
                    
                    col1, col2 = st.columns(2)
                    with col1:
                        st.write(f"**📰 標題**: {result['title'] or '❌ 無法擷取'}")
                        st.write(f"**🏷️ 格式**: {result['style']}")
                    with col2:
                        st.write(f"**🔍 DOI**: {result['doi'] or '❌ 無'}")
                        st.write(f"**✅ 驗證數**: {verified_count}/{total_checks}")
                    
                    if result["sources"]:
                        st.write("**🔗 資料來源檢查結果**:")
                        for source, info in result["sources"].items():
                            status_class = "badge-success" if "✅" in info["status"] else "badge-warning"
                            st.markdown(
                                f'<span class="status-badge {status_class}">{source}: {info["status"]}</span> '
                                f'[🔗 連結]({info["url"]})',
                                unsafe_allow_html=True
                            )

# ========== Tab 3: 統計報告 ==========
with tab3:
    st.header("📊 檢查統計報告")
    
    if not st.session_state.results:
        st.warning("⚠️ 請先完成引用檢查")
    else:
        # 總體統計
        total = len(st.session_state.results)
        fully_verified = sum(
            1 for r in st.session_state.results 
            if r["sources"] and all("✅" in s["status"] for s in r["sources"].values())
        )
        partially_verified = sum(
            1 for r in st.session_state.results 
            if r["sources"] and any("✅" in s["status"] for s in r["sources"].values()) 
            and not all("✅" in s["status"] for s in r["sources"].values())
        )
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
        
        # 資料來源統計
        st.subheader("🔍 資料來源驗證統計")
        source_stats = {}
        for result in st.session_state.results:
            for source, info in result["sources"].items():
                if source not in source_stats:
                    source_stats[source] = {"成功": 0, "失敗": 0}
                if "✅" in info["status"]:
                    source_stats[source]["成功"] += 1
                else:
                    source_stats[source]["失敗"] += 1
        
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
                "驗證來源數": len([s for s in r["sources"].values() if "✅" in s["status"]])
            }
            for source, info in r["sources"].items():
                row[f"{source}_狀態"] = info["status"]
                row[f"{source}_連結"] = info["url"]
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
            summary = f"""
# 學術引用檢查報告

## 📊 總體統計
- 總引用數: {total}
- 完全驗證: {fully_verified} ({fully_verified/total*100:.1f}%)
- 部分驗證: {partially_verified} ({partially_verified/total*100:.1f}%)
- 未驗證: {unverified} ({unverified/total*100:.1f}%)

## 🎯 格式分布
{chr(10).join(f"- {k}: {v}" for k, v in style_counts.items())}

## 🔍 資料來源驗證率
{chr(10).join(f"- {source}: {stats['成功']}/{stats['成功']+stats['失敗']} ({stats['成功']/(stats['成功']+stats['失敗'])*100:.1f}%)" for source, stats in source_stats.items())}
"""
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
    <p>💡 提示：本工具支援 APA、IEEE、MLA 等多種引用格式</p>
    <p>🔒 您的文件僅在本次會話中處理，不會被儲存</p>
</div>
""", unsafe_allow_html=True)