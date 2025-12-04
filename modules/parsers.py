# modules/parsers.py (Cleaned Version)
import re
import unicodedata
import subprocess
import json
import streamlit as st 
# 僅保留用於標題相似度比對的 difflib，但因為它在 api_clients.py 中也需要，
# 我們在這裡可以移除其導入，以防循環依賴。

# ==============================================================================
#                 [ ✨ NEW: ANYSTYLE CLI INTEGRATION ✨ ]
# ==============================================================================
ABSOLUTE_ANYSTYLE_PATH = 'C:\Ruby34\bin\anystyle.bat'
def parse_references_with_anystyle(raw_text_for_anystyle):
    """
    調用 AnyStyle CLI 來解析非結構化的參考文獻文本。
    
    Args:
        raw_text_for_anystyle (str): 包含所有參考文獻的單一大段文本。
        
    Returns:
        tuple: (raw_ref_texts_list, structured_refs_list) 
               - 原始文本列表供顯示
               - 結構化字典列表供 API 調用 (包含 'text', 'title', 'doi', 'type' 等)
    """
    try:
        command = [ABSOLUTE_ANYSTYLE_PATH, 'parse', '--format', 'json']
        
        process = subprocess.run(
            command,
            input=raw_text_for_anystyle.encode('utf-8'),
            capture_output=True,
            text=True, 
            encoding='utf-8', 
            check=True
        )
        
        structured_refs = json.loads(process.stdout)
        
        raw_texts = [ref.get('text', '') for ref in structured_refs]
        
        return raw_texts, structured_refs
        
    except subprocess.CalledProcessError as e:
        st.error("❌ AnyStyle CLI 執行失敗。請確認 AnyStyle 已安裝。")
        st.code(f"錯誤訊息:\n{e.stderr}", language='bash')
        return [], []
    except FileNotFoundError:
        st.error("❌ 找不到 AnyStyle CLI。請確認 'anystyle' 在 PATH 中。")
        return [], []
    except json.JSONDecodeError:
        st.error("❌ AnyStyle 輸出解析錯誤。請檢查 CLI 原始輸出是否為有效 JSON。")
        return [], []

# ==============================================================================
#                 [ 輔助函式: 標題清洗 (供 API Clients 使用) ]
# ==============================================================================

def clean_title(text):
    """標準標題清洗：移除符號、標準化、轉小寫 (供 API 搜尋時的字符串匹配)"""
    dash_variants = ["-", "–", "—", "−", "‑", "‐"]
    for d in dash_variants:
        text = text.replace(d, "")

    text = unicodedata.normalize('NFKC', text)

    cleaned = []
    for ch in text:
        if unicodedata.category(ch)[0] in ("L", "N", "Z"): # L=Letter, N=Number, Z=Space
            cleaned.append(ch.lower())

    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()

def clean_title_for_remedial(text):
    """給補救查詢用的清洗：去掉單獨數字、標點、全形轉半形等"""
    text = unicodedata.normalize('NFKC', text)

    dash_variants = ["-", "–", "—", "−", "‑", "‐"]
    for d in dash_variants:
        text = text.replace(d, "")

    text = re.sub(r'\b\d+\b', '', text) # 移除單獨的數字詞

    cleaned = []
    for ch in text:
        try:
            if unicodedata.category(ch)[0] in ("L", "N", "Z"):
                cleaned.append(ch.lower())
        except TypeError:
            pass 

    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()

# ⚠️ 由於文件上傳功能已移除，所有與文件處理和傳統自定義解析相關的函式 (如 is_valid_year, get_reference_keys, extract_in_text_citations, find_apa_matches 等) 均已從此檔案移除。