# modules/file_processors.py

import re
import fitz  # PyMuPDF
from docx import Document
# 從我們自己的模組中導入
from .parsers import is_appendix_heading, is_reference_head, find_apa, find_apalike, split_multiple_apa_in_paragraph

# ========== Word 處理 ==========
def extract_paragraphs_from_docx(file):
    # 使用 BytesIO 處理 UploadedFile
    doc = Document(file)
    return [para.text.strip() for para in doc.paragraphs if para.text.strip()]

# ========== PDF 處理 ==========
def extract_paragraphs_from_pdf(file):
    text = ""
    with fitz.open(stream=file.read(), filetype="pdf") as doc:
        for page in doc:
            page_text = page.get_text("text")
            text += page_text + "\n"
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return paragraphs

# ========== 萃取參考文獻 ==========
def extract_reference_section_from_bottom(paragraphs, start_keywords=None):
    """
    從底部往上找出參考文獻區段起點。
    [MODIFIED] 回傳 (body_paragraphs, reference_paragraphs, matched_keyword)
    """
    if start_keywords is None:
        start_keywords = [
            "參考文獻", "參考資料", "references", "reference",
            "bibliography", "works cited", "literature cited",
            "references and citations"
        ]

    for i in reversed(range(len(paragraphs))):
        para = paragraphs[i].strip()

        # 跳過太長或包含標點的段落（可能是正文）
        if len(para) > 30 or re.search(r'[.,;:]', para):
            continue

        normalized = para.lower()
        if normalized in start_keywords:
            # [!] 找到分割點 i
            body_paragraphs = paragraphs[:i]
            reference_paragraphs = clip_until_stop(paragraphs[i + 1:])
            return body_paragraphs, reference_paragraphs, para

    # [!] 未找到
    return paragraphs, [], None # 假設整個都是內文，沒有參考文獻


# ========== 萃取參考文獻 (加強版) ==========
def clip_until_stop(paragraphs_after):
    result = []
    for para in paragraphs_after:
        if is_appendix_heading(para):
            break
        result.append(para)
    return result

def extract_reference_section_improved(paragraphs):
    """
    改進的參考文獻區段識別，從底部往上掃描。
    [MODIFIED] 返回：(body_paragraphs, reference_paragraphs, 識別到的標題, 識別方法)
    """

    def is_reference_format(text):
        text = text.strip()
        if len(text) < 10:
            return False
        if re.search(r'\(\d{4}[a-c]?\)', text):  # APA 年份格式
            return True
        if re.match(r'^\[\d+\]', text):      # IEEE 編號格式
            return True
        if re.search(r'[A-Z][a-z]+,\s*[A-Z]\.', text):  # 作者名樣式
            return True
        return False

    reference_keywords = [
        "參考文獻", "references", "reference",
        "bibliography", "works cited", "literature cited",
        "references and citations", "參考文獻格式"
    ]

    # ✅ 從底部往上掃描
    for i in reversed(range(len(paragraphs))):
        para = paragraphs[i].strip()
        para_lower = para.lower()
        para_nospace = re.sub(r'\s+', '', para_lower)
        
        found = False

        # ✅ 純標題相符（e.g. "References"）
        if para_lower in reference_keywords:
            found = True
            method = "純標題識別（底部）"

        # ✅ 容錯標題（含章節編號）
        elif re.match(
            r'^((第?[一二三四五六七八九十百千萬壹貳參肆伍陸柒捌玖拾佰仟萬]+章[、．.︑,，]?)|(\d+|[IVXLCDM]+|[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]+)?[、．.︑,， ]?)?\s*(參考文獻|參考資料|references?|bibliography|works cited|literature cited|references and citations)\s*$',
            para_lower
        ):
            found = True
            method = "章節標題識別（底部）"

        # ✅ 模糊關鍵字 + 後面段落像 APA 格式
        elif any(para_lower.strip() == k for k in ["reference", "參考", "bibliography", "文獻"]):
            if i + 1 < len(paragraphs):
                next_paras = paragraphs[i+1:i+6]
                if sum(1 for p in next_paras if is_reference_format(p)) >= 1:
                    found = True
                    method = "模糊標題+內容識別"

        if found:
            # [!] 找到分割點 i
            body_paragraphs = paragraphs[:i]
            reference_paragraphs = clip_until_stop(paragraphs[i + 1:])
            return body_paragraphs, reference_paragraphs, para.strip(), method

    # [!] 未找到
    return paragraphs, [], None, "未找到參考文獻區段"


# ========== 段落合併器 ==========
def detect_and_split_ieee(paragraphs):
    """
    若第一段為 IEEE 格式 [1] 開頭，則將整段合併並依據 [數字] 切割
    """
    if not paragraphs:
        return None

    first_line = paragraphs[0].strip()
    if not re.match(r'^\[\d+\]', first_line):
        return None

    full_text = ' '.join(paragraphs)  # 將換行視為空格
    refs = re.split(r'(?=\[\d+\])', full_text)  # 用 lookahead 保留切割點
    return [r.strip() for r in refs if r.strip()]

def merge_references_by_heads(paragraphs):
    merged = []
    
    # 新增一個 regex 來過濾掉純數字/標點的 "行"
    # 這些是 PDF 剖析錯誤，例如 "3." 或 "4."
    stray_number_pattern = re.compile(r"^\s*(\d{1,3}[.)、．]?|[IVXLCDM]+[.)、．]?)\s*$")

    for para in paragraphs:
        
        # 如果這一行只是個 stray 數字，就直接跳過
        if stray_number_pattern.match(para):
            continue

        # 統一取得 APA 和 APA_LIKE 所有年份 match
        apa_count = 1 if find_apa(para) else 0
        apalike_count = len(find_apalike(para))

        if apa_count >= 2 or apalike_count >= 2:
            sub_refs = split_multiple_apa_in_paragraph(para)
            merged.extend([s.strip() for s in sub_refs if s.strip()])
        else:
            if is_reference_head(para):
                merged.append(para.strip())
            else:
                if merged:
                    merged[-1] += " " + para.strip()
                else:
                    # 避免將無關的文字（例如頁尾）加入
                    pass
                    # merged.append(para.strip()) # 舊邏輯
    return merged
