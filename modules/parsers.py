# modules/parsers.py

import re
import unicodedata
from difflib import SequenceMatcher

# ========== [已刪除] ==========
# extract_doi
# is_valid_year
# is_appendix_heading
# find_apa, match_apa_title_section, find_apa_matches
# find_apalike, match_apalike_title_section, find_apalike_matches
# get_reference_keys, extract_in_text_citations
# detect_reference_style
# is_reference_head, split_multiple_apa_in_paragraph
# extract_title
#
# (所有這些功能現在都由 modules/gemini_client.py 處理)


# ========== 清洗標題 (保留) ==========
# 這些被 api_clients.py 用於 SerpAPI 查詢
def clean_title(text):
    # 移除 dash 類符號
    dash_variants = ["-", "–", "—", "−", "‑", "‐"]
    for d in dash_variants:
        text = text.replace(d, "")

    # 標準化字符（例如全形轉半形）
    text = unicodedata.normalize('NFKC', text)

    # 過濾掉標點符號、符號類別（不刪文字！）
    cleaned = []
    for ch in text:
        if unicodedata.category(ch)[0] in ("L", "N", "Z"):  # L=Letter, N=Number, Z=Space
            cleaned.append(ch.lower())
        # else: 跳過標點與符號

    # 統一空白
    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()

# 專門給補救命中的清洗 (保留)
def clean_title_for_remedial(text):
    """給補救查詢用的清洗：去掉單獨數字、標點、全形轉半形等"""
    # 標準化字元（全形轉半形）
    text = unicodedata.normalize('NFKC', text)

    # 移除 dash 類符號
    dash_variants = ["-", "–", "—", "−", "‑", "‐"]
    for d in dash_variants:
        text = text.replace(d, "")

    # 移除單獨的數字詞（如頁碼、卷號）
    text = re.sub(r'\b\d+\b', '', text)

    # 保留字母、數字、空白
    cleaned = []
    for ch in text:
        try:
            if unicodedata.category(ch)[0] in ("L", "N", "Z"):  # L=Letter, N=Number, Z=Space
                cleaned.append(ch.lower())
        except TypeError:
            pass # Handle potential errors with unicodedata

    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()