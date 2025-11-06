import re
import unicodedata
from difflib import SequenceMatcher

# ========== 擷取 DOI ==========
def extract_doi(text):
    match = re.search(r'(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', text, re.I)
    if match:
        return match.group(1).rstrip(".")

    doi_match = re.search(r'doi:\s*(https?://doi\.org/)?(10\.\d{4,9}/[-._;()/:A-Z0-9]+)', text, re.I)
    if doi_match:
        return doi_match.group(2).rstrip(".")

    return None

# ========================================= 所有規則封裝  =========================================
# ========== 年份規則 ==========
def is_valid_year(year_str):
    try:
        year = int(year_str)
        return 1000 <= year <= 2050
    except:
        return False
    
# ========== 抓附錄 ========== 
def is_appendix_heading(text):
    text = text.strip()
    return bool(re.match(
        r'^([【〔（(]?\s*)?((\d+|[IVXLCDM]+|[一二三四五六七八九十壹貳參肆伍陸柒捌玖拾]+)[、．. ]?)?\s*(附錄|APPENDIX)(\s*[】〕）)]?)?$',
        text,
        re.IGNORECASE
    ))

# ========== APA規則 ==========    
def find_apa(ref_text):
    """
    判斷一段參考文獻是否為 APA 格式（標準括號年份 or n.d.）
    標準格式：Lin, J. (2020). Title.
    支援變體：中英文括號、句號符號、n.d. 年份
    """
    apa_match = re.search(r'[（(](\d{4}[a-c]?|n\.d\.)[）)]?[。\.]?', ref_text, re.IGNORECASE)
    if not apa_match:
        return False

    year_str = apa_match.group(1)[:4]
    year_pos = apa_match.start(1)

    # 避免像 887(2020) 這種前方是數字的情況
    pre_context = ref_text[max(0, year_pos - 5):year_pos]
    if re.search(r'\d', pre_context):
        return False

    if year_str.isdigit():
        return is_valid_year(year_str)
    return apa_match.group(1).lower() == "n.d."

def match_apa_title_section(ref_text):
    """
    擷取 APA 結構中的標題段落（位於年份後）
    範例：Lin, J. (2020). Title here.
    - 支援標點：.、。 、,
    - 避免誤抓數字中的逗號或句號
    """
    return re.search(
        r'[（(](\d{4}[a-c]?|n\.d\.)[）)]\s*[\.,，。]?\s*(.+?)(?:(?<!\d)[,，.。](?!\d)|$)',
        ref_text,
        re.IGNORECASE
    )

def find_apa_matches(ref_text):
    """
    回傳符合 APA 格式的年份 match（含位置、原文等）
    """
    APA_PATTERN = r'[（(](\d{4}[a-c]?|n\.d\.)[）)]?[。\.]?'
    matches = []
    for m in re.finditer(APA_PATTERN, ref_text, re.IGNORECASE):
        year_str = m.group(1)[:4]
        year_pos = m.start(1)
        pre_context = ref_text[max(0, year_pos - 5):year_pos]
        if re.search(r'\d', pre_context):
            continue
        if year_str.isdigit() and is_valid_year(year_str):
            matches.append(m)
        elif m.group(1).lower() == "n.d.":
            matches.append(m)
    return matches


# ========== APA_LIKE規則 ==========
def find_apalike(ref_text):
    valid_years = []

    # 類型 1：標點 + 年份 + 標點（常見格式）
    for match in re.finditer(r'[,，.。]\s*(\d{4}[a-c]?)[.。，]', ref_text):
        year_str = match.group(1)
        year_pos = match.start(1)
        year_core = year_str[:4]
        if not is_valid_year(year_core):
            continue

        # 前 5 字元不能有數字（排除 3.2020. 類型）
        pre_context = ref_text[max(0, year_pos - 5):year_pos]
        if re.search(r'\d', pre_context):
            continue

        # 若年份後 5 字元是 .加數字，或像 .v06、.abc 等常見 DOI 結尾，則排除
        after_context = ref_text[match.end(1):match.end(1) + 5]
        if re.match(r'\.(\d{1,2}|[a-z0-9]{2,})', after_context, re.IGNORECASE):
            continue

        # 排除 arXiv 尾巴，例如 arXiv:xxxx.xxxxx, 2023
        arxiv_pattern = re.compile(
            r'arxiv:\d{4}\.\d{5}[^a-zA-Z0-9]{0,3}\s*[,，]?\s*' + re.escape(year_str),
            re.IGNORECASE
        )
        arxiv_match = arxiv_pattern.search(ref_text)
        if arxiv_match and arxiv_match.start() < year_pos:
            continue

        valid_years.append((year_str, year_pos))

    # 類型 2：特殊格式「，2020，。」（中文常見）
    for match in re.finditer(r'，\s*(\d{4}[a-c]?)\s*，\s*。', ref_text):
        year_str = match.group(1)
        year_pos = match.start(1)
        year_core = year_str[:4]
        if not is_valid_year(year_core):
            continue
        pre_context = ref_text[max(0, year_pos - 5):year_pos]
        if re.search(r'\d', pre_context):
            continue
        valid_years.append((year_str, year_pos))

    return valid_years

def match_apalike_title_section(ref_text):
# 類型 1：常見格式（, 2020. Title.）
    match = re.search(
        r'[,，.。]\s*(\d{4}[a-c]?)(?:[.。，])+\s*(.*?)(?:(?<!\d)[,，.。](?!\d)|$)',
        ref_text
    )
    if match:
        return match

    # 類型 2：特殊中文格式（，2020，。Title）
    return re.search(
        r'，\s*(\d{4}[a-c]?)\s*，\s*。[ \t]*(.+?)(?:[，。]|$)',
        ref_text
    )

def find_apalike_matches(ref_text):
    """
    回傳符合 APA_LIKE 格式的年份 match（含位置、原文等）
    """
    matches = []

    # 類型 1：標點 + 年份 + 標點（常見格式）
    pattern1 = r'[,，.。]\s*(\d{4}[a-c]?)[.。，]'
    for m in re.finditer(pattern1, ref_text):
        year_str = m.group(1)
        year_pos = m.start(1)
        year_core = year_str[:4]
        if not is_valid_year(year_core):
            continue
        pre_context = ref_text[max(0, year_pos - 5):year_pos]
        after_context = ref_text[m.end(1):m.end(1) + 5]
        if re.search(r'\d', pre_context):
            continue
        # 新增條件：年份後若接 DOI 型式則排除
        if re.match(r'\.(\d{1,2}|[a-z0-9]{2,})', after_context, re.IGNORECASE):
            continue
        arxiv_pattern = re.compile(
            r'arxiv:\d{4}\.\d{5}[^a-zA-Z0-9]{0,3}\s*[,，]?\s*' + re.escape(year_str),
            re.IGNORECASE
        )
        if arxiv_pattern.search(ref_text) and arxiv_pattern.search(ref_text).start() < year_pos:
            continue
        matches.append(m)

    # 類型 2：特殊中文格式「，2020，。」
    pattern2 = r'，\s*(\d{4}[a-c]?)\s*，\s*。'
    for m in re.finditer(pattern2, ref_text):
        year_str = m.group(1)
        year_pos = m.start(1)
        year_core = year_str[:4]  # ✅ 補上 year_core
        pre_context = ref_text[max(0, year_pos - 5):year_pos]
        if re.search(r'\d', pre_context):
            continue
        if is_valid_year(year_core):
            matches.append(m)

    return matches


# ================================================================================================
# ==================== [ ✨ MODIFIED FUNCTION: Citation Matching ✨ ] ====================
# ================================================================================================

def get_reference_keys(ref_text): # [!] 改為複數 "keys"
    """
    為一條參考文獻產生所有可能的 "key"。
    [MODIFIED] 返回一個 key 列表, e.g., ["num:1", "apa:gao:2023"]
    """
    ref_text = ref_text.strip()
    keys = []
    
    # 策略 1：偵測數字索引鍵 (例如 "1." 或 "[1]")
    numeric_match = re.match(r'^\s*(?:\[(\d+)\]|(\d+)[.)、．])', ref_text)
    if numeric_match:
        key_num = numeric_match.group(1) or numeric_match.group(2)
        keys.append(f"num:{key_num}")

    # 策略 2：偵測 APA 索引鍵 (Author, YYYY)
    
    # [!] NEW: First, check for ISO standards, as they have special formatting.
    # Handles "32. ISO/IEC 27005:2022, 7.2"
    # Handles "ISO/IEC 27001. (2022)."
    iso_author = None
    iso_year = None
    
    # Try to find "ISO/IEC 27005:2022" (no parens)
    iso_match_with_year = re.search(r'(ISO(?:/IEC)?\s*[\d-]+):(\d{4})', ref_text, re.IGNORECASE)
    if iso_match_with_year:
        iso_author = iso_match_with_year.group(1).lower().replace(' ', '').replace('/', '').replace('iec', '') # "iso27005"
        iso_year = iso_match_with_year.group(2).lower() # "2022"
    else:
        # Try to find "ISO/IEC 27001. (2022)." (with parens)
        iso_match_no_year = re.search(r'(ISO(?:/IEC)?\s*[\d-]+)', ref_text, re.IGNORECASE)
        year_match_paren = re.search(r'[（(](\d{4})[）)]', ref_text)
        if iso_match_no_year and year_match_paren:
            iso_author = iso_match_no_year.group(1).lower().replace(' ', '').replace('/', '').replace('iec', '') # "iso27001"
            iso_year = year_match_paren.group(1).lower() # "2022"

    if iso_author and iso_year:
        iso_key = f"apa:{iso_author}:{iso_year}"
        if iso_key not in keys:
            keys.append(iso_key)
            
    # [!] Standard APA logic, but skip if we already found an ISO match
    # 並且只抓取 "Author, Y... (YYYY)" 這種標準作者格式
    if not iso_author: 
        year_match = re.search(r'[（(](\d{4}[a-c]?|n\.d\.)[）)]', ref_text)
        year = None
        if year_match:
            year = year_match.group(1).lower().rstrip('abc')

        # [!] 修正為更嚴格的作者匹配 (e.g., Gao, Y. or Lewis, P.)
        author_match = re.match(r'^\s*([A-Z][a-z]+(?:,\s*[A-Z]\.)?)(?:,\s*[A-Z][a-z]+)?', ref_text, re.IGNORECASE)
        author = None
        if author_match:
            author = author_match.group(1).split(',')[0].lower()
        # Fallback for "International Organization..."
        elif re.match(r'^\s*(International Organization for Standardization)', ref_text, re.IGNORECASE):
             author_match = re.match(r'^\s*(International Organization for Standardization)', ref_text, re.IGNORECASE)
             author = "internationalorganizationforstandardization"
        # Fallback for "經濟部..."
        elif re.match(r'^\s*(經濟部標準檢驗局)', ref_text, re.IGNORECASE):
             author = "經濟部標準檢驗局"


        if author and year:
            apa_key = f"apa:{author}:{year}"
            if apa_key not in keys:
                keys.append(apa_key)

    return keys

def extract_in_text_citations(body_paragraphs):
    """
    從內文段落中擷取所有 "in-text" 引用索引鍵。
    [MODIFIED] 支援全形括號 `（ ）`
    """
    full_text = " ".join(body_paragraphs)
    found_keys = set()

    # 策略 1：尋找數字引用 e.g., [1], [1, 2], [1-5], [1, 5-7]
    # (邏輯保持不變, 假設都使用半形 [])
    NUMERIC_PATTERN = re.compile(r'\[([\d,\s-]+)\]')
    for match in NUMERIC_PATTERN.finditer(full_text):
        content = re.sub(r'\s+', '', match.group(1))
        parts = re.split(r'[,;]', content)
        for part in parts:
            if '-' in part:
                range_parts = part.split('-')
                if len(range_parts) == 2:
                    try:
                        start = int(range_parts[0])
                        end = int(range_parts[1])
                        if end > start + 50: continue # Sanity check
                        for i in range(start, end + 1):
                            found_keys.add(f"num:{i}")
                    except ValueError: pass
            else:
                try:
                    num = int(part)
                    found_keys.add(f"num:{num}")
                except ValueError: pass

    # ==================== [ ✨ MODIFICATION START ✨ ] ====================
    # 策略 2：尋找 (Author et al., YYYY)
    # [MODIFIED] 支援全形括號 `（ ）`
    APA_ETAL_PATTERN = re.compile(r'[（(]([^)）]*?et al\.[^)）]*?,\s*(\d{4}[a-z]?|n\.d\.)[^)）]*)[）)]', re.IGNORECASE)
    for match in APA_ETAL_PATTERN.finditer(full_text):
        content = match.group(1)
        citations = re.split(r';\s*', content)
        for cit in citations:
            cit_parts = re.search(r'([A-Z][a-z]+)(?:,\s*[A-Z]\.)?\s+et al\.,\s*(\d{4}[a-z]?|n\.d\.)', cit, re.IGNORECASE)
            if cit_parts:
                author = cit_parts.group(1).lower()
                year = cit_parts.group(2).lower().rstrip('abc')
                found_keys.add(f"apa:{author}:{year}") # Key: "apa:huang:2024"

    # 策略 3：尋找 (Author, YYYY) 或 (Organization, YYYY)
    # [MODIFIED] 支援全形括號 `（ ）`
    APA_SINGLE_PATTERN = re.compile(r'[（(]([^)）]*?,\s*(\d{4}[a-z]?|n\.d\.)[^)）]*)[）)]', re.IGNORECASE)
    for match in APA_SINGLE_PATTERN.finditer(full_text):
        content = match.group(1)
        if 'et al.' in content: continue # 已被上面處理
        
        citations = re.split(r';\s*', content)
        for cit in citations:
            # 匹配 "Gao, 2023" 或 "International Organization..., 2022"
            cit_parts = re.search(r'([A-Z][A-Z a-z]+?)(?:,\s*[A-Z]\.)?,\s*(\d{4}[a-z]?|n\.d\.)', cit, re.IGNORECASE)
            if cit_parts:
                author = cit_parts.group(1).split(',')[0].lower()
                year = cit_parts.group(2).lower().rstrip('abc')
                found_keys.add(f"apa:{author}:{year}") # Key: "apa:gao:2023"

            # 處理 (ISO/IEC 27001:2022, 0.2) 這種
            iso_parts = re.search(r'(ISO(?:/IEC)?\s*[\d-]+):(\d{4})', cit, re.IGNORECASE)
            if iso_parts:
                author = iso_parts.group(1).lower().replace(' ', '').replace('/', '').replace('iec', '')
                year = iso_parts.group(2).lower().rstrip('abc')
                found_keys.add(f"apa:{author}:{year}") # e.g., apa:iso27001:2022

    # 策略 4：尋找 Author (YYYY) 格式
    # [MODIFIED] 支援全形括號 `（ ）`
    APA_PAREN_YEAR_PATTERN = re.compile(r'([A-Z][a-z]+(?:,\s*[A-Z]\.)?)\s+(?:et al\.)?\s*[（(]([^)）]*?)(\d{4}[a-z]?|n\.d\.)([^)）]*?)[）)]', re.IGNORECASE)
    for match in APA_PAREN_YEAR_PATTERN.finditer(full_text):
        content_before = match.group(2).strip()
        content_after = match.group(4).strip()
        
        if content_before == "" and content_after == "":
            author = match.group(1).split(',')[0].lower() # "Lewis, P." -> "lewis"
            year = match.group(3).lower().rstrip('abc')
            found_keys.add(f"apa:{author}:{year}") # Key: "apa:lewis:2020"
    # ==================== [ ✨ MODIFICATION END ✨ ] ====================

    return found_keys


# ========== 清洗標題 ==========
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

# 專門給補救命中的清洗
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

# ========== 偵測格式 ==========
def detect_reference_style(ref_text):
    # IEEE 通常開頭是 [1]，或含有英文引號 "標題"
    if re.match(r'^\[\d+\]', ref_text) or '"' in ref_text:
        return "IEEE"

    # APA：使用封裝後的 find_apa()
    if find_apa(ref_text):
        return "APA"

    # APA_LIKE：使用封裝後的 find_apalike()
    if find_apalike(ref_text):
        return "APA_LIKE"

    return "Unknown"

# ========== 段落合併器（PDF 專用，根據參考文獻開頭切分） ==========
def is_reference_head(para):
    """
    判斷段落是否為參考文獻開頭（IEEE 或編號格式）
    [FINAL FIX]
    - 移除 APA/APA_LIKE 檢查，因為 "Author... (YYYY)" 格式
    - 可能是續行，不一定是開頭。
    - 唯一的開頭標記是 [數字] 或 數字.。
    """
    para_stripped = para.strip()
    if not para_stripped:
        return False

    # 規則 1：判斷 IEEE 格式 [數字]
    if re.match(r"^\[\d+\]", para_stripped):
        return True
        
    # 規則 2：偵測數字編號開頭的格式 (例如 "1." 或 "15.")
    if re.match(r"^\d{1,3}[.)、．]\s+", para_stripped):
        # 必須保留這個長度檢查，
        # 才能過濾掉 "4." 這種 PDF 換行產生的純數字行
        if len(para_stripped) > 10: 
            return True
    
    # [REMOVED] 
    # 移除了所有 find_apa 和 find_apalike 檢查，
    # 因為它們會錯誤地將 "續行" 判斷為 "開頭"。
    
    # 預設：不是開頭
    return False

def split_multiple_apa_in_paragraph(paragraph):
    """
    改良版：從出現第 2 筆 APA 或 APA_LIKE 年份起，每筆往前固定 5 字元切段。
    - APA： (2020)、(2020a)、(n.d.)
    - APA_LIKE： , 2020. 或 .2020. 等，且前 5 字元不能含數字
    """

    # 使用統一封裝函數找出所有 APA 與 APA_LIKE 的 matches
    apa_matches = find_apa_matches(paragraph)
    apalike_matches = find_apalike_matches(paragraph)

    all_matches = apa_matches + apalike_matches
    all_matches.sort(key=lambda m: m.start())

    # 若不到 2 筆則不切
    if len(all_matches) < 2:
        return [paragraph]

    # 每筆從前面固定回推 5 字元切割
    split_indices = []
    for match in all_matches[1:]:  # 從第 2 筆開始切
        cut_index = max(0, match.start() - 5)
        split_indices.append(cut_index)

    segments = []
    start = 0
    for idx in split_indices:
        segments.append(paragraph[start:idx].strip())
        start = idx
    segments.append(paragraph[start:].strip())

    return [s for s in segments if s]



# ========== 擷取標題 ==========
def extract_title(ref_text, style):
    if style == "APA":
        match = match_apa_title_section(ref_text)
        if match:
            year_str = match.group(1)[:4]
            if year_str.isdigit() and not is_valid_year(year_str):
                return None
            return match.group(2).strip(" ,。")

    elif style == "IEEE":
        matches = re.findall(r'"([^"]+)"', ref_text)
        if matches:
            return max(matches, key=len).strip().rstrip(",.")
        fallback = re.search(r'(?<!et al)([A-Z][^,.]+[a-zA-Z])[,\.]', ref_text)
        if fallback:
            return fallback.group(1).strip(" ,.")

    elif style == "APA_LIKE":
        match = match_apalike_title_section(ref_text)
        if match:
            year_str = match.group(1)
            after_fragment = ref_text[match.end(1):match.end(1)+5]
            if is_valid_year(year_str) and not re.match(r'\.\d', after_fragment):
                return match.group(2).strip(" ,。")

    return None

