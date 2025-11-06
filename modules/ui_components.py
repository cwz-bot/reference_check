# modules/ui_components.py

import streamlit as st
# 從我們自己的模組導入
from .parsers import (
    detect_reference_style, extract_title, extract_doi,
    find_apa_matches, find_apalike_matches
)

# ========== 分析單筆參考文獻用（含 APA_LIKE 年份統計） ==========
def analyze_single_reference(ref_text, ref_index):
    style = detect_reference_style(ref_text)
    title = extract_title(ref_text, style)
    doi = extract_doi(ref_text)

    # APA 與 APA_LIKE 年份標註（高亮）
    highlights = ref_text
    # 所有 match 統一加入，並根據位置從後往前高亮，避免重疊 offset 錯亂
    all_year_matches = find_apa_matches(ref_text) + find_apalike_matches(ref_text)
    all_year_matches.sort(key=lambda m: m.start(), reverse=True)
    for match in all_year_matches:
        start, end = match.span()
        highlights = highlights[:start] + "**" + highlights[start:end] + "**" + highlights[end:]

    # === 年份統計 ===
    apa_year_count = len(find_apa_matches(ref_text))
    apalike_year_count = len(find_apalike_matches(ref_text))
    year_count = apa_year_count + apalike_year_count

    # === 輸出到 UI ===
    st.markdown(f"**{ref_index}.**")
    st.write(highlights)
    st.markdown(f"""
    • 📰 **擷取標題**：{title if title else "❌ 無法擷取"}  
    • 🔍 **擷取 DOI**：{doi if doi else "❌ 無 DOI"}  
    • 🏷️ **偵測風格**：{style}  
    • 📅 **年份出現次數**：{year_count}  
    """)

    return (ref_text, title) if title else None