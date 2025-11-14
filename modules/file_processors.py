# modules/file_processors.py

import re
import fitz  # PyMuPDF
from docx import Document
# [修改] 移除了所有 parsers 的依賴

# ========== Word 處理 (不變) ==========
def extract_paragraphs_from_docx(file):
    doc = Document(file)
    return [para.text.strip() for para in doc.paragraphs if para.text.strip()]

# ========== PDF 處理 (不變) ==========
def extract_paragraphs_from_pdf(file):
    text = ""
    with fitz.open(stream=file.read(), filetype="pdf") as doc:
        for page in doc:
            page_text = page.get_text("text")
            text += page_text + "\n"
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return paragraphs

# ========== [已刪除] ==========
# extract_reference_section_improved
# clip_until_stop
# extract_reference_section_from_bottom
# detect_and_split_ieee
# merge_references_by_heads
#
# (所有這些功能現在都由 modules/gemini_client.py 處理)