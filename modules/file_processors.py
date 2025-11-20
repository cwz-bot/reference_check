# modules/file_processors.py

import re
import fitz  # PyMuPDF
from docx import Document


# ============================================================
# 共用：抓取 REFERENCES 區塊後的所有內容
# ============================================================

def extract_references_section(paragraphs):
    ref_keywords = [
        "references", "reference", "bibliography",
        "參考文獻", "参考文献", "參考資料", "参考资料"
    ]

    start_idx = None

    for i, p in enumerate(paragraphs):
        # 正規化：移除非字母數字、轉小寫
        normalized = "".join(ch.lower() for ch in p if ch.isalnum())

        for kw in ref_keywords:
            kw_norm = "".join(ch.lower() for ch in kw if ch.isalnum())
            if kw_norm in normalized:
                start_idx = i
                break

        if start_idx is not None:
            break
    
    # 找不到 REF，就回空陣列（照你的需求）
    if start_idx is None:
        return []

    # 回傳 REFERENCES 後的所有 content
    return paragraphs[start_idx:]


# ============================================================
# DOCX 處理
# ============================================================

def extract_paragraphs_from_docx(file):
    doc = Document(file)
    paragraphs = [para.text.strip() for para in doc.paragraphs if para.text.strip()]
    return extract_references_section(paragraphs)


# ============================================================
# PDF 處理
# ============================================================

def extract_paragraphs_from_pdf(file):
    text = ""

    pdf_bytes = file.read()

    with fitz.open(stream=pdf_bytes, filetype="pdf") as doc:
        for page in doc:
            text += page.get_text("text") + "\n"

    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return extract_references_section(paragraphs)

