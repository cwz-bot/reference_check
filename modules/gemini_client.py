# modules/gemini_client.py

import streamlit as st
import google.generativeai as genai
import time
import json
import re

# --- 新增常數：設定最大區塊尺寸 ---
MAX_REF_CHUNK_SIZE = 8000 # Max characters per chunk to send to the LLM. 

# --- 金鑰管理 ---
def get_gemini_key():
    """從 Streamlit secrets 或本地檔案獲取 Gemini API 金鑰"""
    try:
        return st.secrets["gemini_api_key"]
    except (KeyError, FileNotFoundError):
        try:
            with open("gemini_key.txt", "r") as f:
                return f.read().strip()
        except FileNotFoundError:
            st.error("❌ 找不到 Gemini API 金鑰，請確認已設定 secrets 或提供 gemini_key.txt")
            st.stop()

def get_gemini_model():
    """初始化並返回 Gemini Pro 模型實例"""
    try:
        api_key = get_gemini_key()
        genai.configure(api_key=api_key)
        
        safety_settings = [
            {"category": "HARM_CATEGORY_HARASSMENT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_HATE_SPEECH", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_SEXUALLY_EXPLICIT", "threshold": "BLOCK_NONE"},
            {"category": "HARM_CATEGORY_DANGEROUS_CONTENT", "threshold": "BLOCK_NONE"},
        ]
        
        # 這裡不需要 JSON Mime Type，因為Task 1 & 1.5 需要文本輸出
        model = genai.GenerativeModel(
            'gemini-2.5-pro',
            safety_settings=safety_settings,
        )
        return model
    except Exception as e:
        st.error(f"❌ 初始化 Gemini 模型失敗：{e}")
        st.stop()

# --- 核心 Prompt ---

# 任務 1: 從全文中找出參考文獻區段
PROMPT_TASK_1_LOCATE = """
你是一個學術文件解析器。你的任務是從以下提供的文件全文段落中，準確找到「參考文獻」(References) 區段的起始位置。

規則：
1. 參考文獻區段通常在文件的最後 30%。
2. 區段標題可能是 "References", "參考文獻", "REFERENCE", "Bibliography" 等。
3. 你的**唯一**輸出必須是參考文獻區段（包含其標題）之後的所有原始文本。
4. 如果找不到，請返回空字符串。

這是文件段落 (使用 "---" 分隔)：
---
{full_text}
---
"""

# [新增任務 1.5]：過濾非學術文獻，返回純文本列表
PROMPT_TASK_1_5_FILTER = """
你是一個學術引用過濾器。我將提供一段從 PDF/Word 提取的參考文獻原始文本。

你的任務是：
1. 識別每一筆獨立的引用條目。
2. 判斷該條目是否為傳統學術文獻 (如期刊/會議論文/學位論文/書/正式報告)。
3. **過濾並移除**所有非學術文獻的引用，例如網站、部落格文章、軟體安裝頁面、個人社群媒體等。
4. 將**所有學術文獻**的原始文本，以 Python 列表 (list) 的形式返回。

返回格式要求：
- 必須是有效的 JSON 格式，且為一個單一的列表 (list)。
- 列表中的每個元素都是一條被判斷為**學術文獻**的完整原始文本字串（包含合併的換行符）。
- **即使沒有發現任何學術引用，也請返回空的 JSON 陣列 `[]`。**

這是原始文本：
---
{reference_text}
---
"""

# 任務 2: 解析參考文獻為結構化 JSON (只處理學術文獻)
PROMPT_TASK_2_PARSE = """
你是一個精確的學術引用解析器。我將提供一段從 PDF/Word 提取的**學術參考文獻**原始文本。

你的任務是：
1. 閱讀所有文本，將跨越多行的引用合併為一個單一的條目。
2. 識別每一筆獨立的參考文獻。
3. 對於**每一筆**文獻，提取以下五個欄位（因為已經過濾，is_academic 欄位固定為 true）：
    - "text": 完整的參考文獻字符串（合併換行後）。
    - "title": 該文獻的標題。
    - "doi": 該文獻的 DOI (如果沒有則為 null)。
    - "url": 該文獻的主要 URL (如果沒有則為 null)。
    - "style": 偵測到的格式。範例："Journal Article", "Conference Paper", "Book", "Report", "Thesis"。
4. 最終，以一個 JSON 陣列 (array) 的形式返回所有獨立的參考文獻物件。**即使沒有發現任何引用，也請返回空的 JSON 陣列 `[]`。**

這是原始文本：
---
{reference_text}
---
"""

def chunk_reference_text(raw_text: str, chunk_size: int) -> list[str]:
    """將參考文獻的原始文本分割成較小的區塊，嘗試在引用的結尾處分割。"""
    # 此函數保持不變
    
    # 使用換行符號 ('\n') 作為主要分割依據，避免在單一引用中間切斷
    chunks = []
    current_index = 0
    
    while current_index < len(raw_text):
        end_index = min(current_index + chunk_size, len(raw_text))
        
        # 如果不是在文本末尾，則往回找一個良好的分割點
        if end_index < len(raw_text):
            # 尋找最近的兩個換行符 ('\n\n')
            split_point = raw_text.rfind('\n\n', current_index, end_index)
            if split_point == -1:
                # 尋找最近的單個換行符
                split_point = raw_text.rfind('\n', current_index, end_index)
            
            # 確保切點不會讓區塊太小，且不是在起始位置
            if split_point > current_index + chunk_size / 2:
                end_index = split_point
        
        # 增加區塊
        chunks.append(raw_text[current_index:end_index].strip())
        current_index = end_index + 1 # 從切點之後開始下一個區塊
    
    # 過濾掉空字符串
    return [c for c in chunks if c]


def parse_document_with_gemini(model, paragraphs):
    """
    使用 Gemini 執行三階段解析：
    1. 找出參考文獻區段 (Task 1)
    2. 過濾非學術引用 (Task 1.5)
    3. 解析該學術區段為結構化資料 (Task 2)，使用區塊化 (Chunking) 來處理。
    
    返回: (list[dict] | None, str) -> (解析結果, 除錯訊息)
    """
    
    # --- 階段 1：定位參考文獻區段 ---
    total_paras = len(paragraphs)
    start_index = max(0, int(total_paras * 0.6))
    search_text = "\n---\n".join(paragraphs[start_index:])
    
    try:
        prompt1 = PROMPT_TASK_1_LOCATE.format(full_text=search_text)
        response1 = model.generate_content(prompt1)
        refs_raw_text = response1.text.strip()
        
        if not refs_raw_text:
            return None, "Gemini 未能定位到參考文獻區段。"
            
    except Exception as e:
        return None, f"Gemini 呼叫失敗 (階段 1): {e}"


    # --- [新增] 階段 1.5：過濾非學術引用 ---
    try:
        prompt1_5 = PROMPT_TASK_1_5_FILTER.format(reference_text=refs_raw_text)
        
        # 暫時啟用 JSON 輸出，確保輸出是列表
        config_json = {"response_mime_type": "application/json"}
        response1_5 = model.generate_content(prompt1_5, generation_config=config_json)
        
        clean_json_text = re.sub(r'```json\n(.*?)\n```', r'\1', response1_5.text, flags=re.DOTALL)
        academic_refs_list = json.loads(clean_json_text)
        
        if not academic_refs_list:
             return [], "Gemini 過濾後沒有找到任何學術引用。" # 返回空列表，流程繼續
        
        # 將過濾後的列表重新合併為一個大的字符串，準備 Chunking
        academic_refs_text = "\n\n".join(academic_refs_list) 
        st.info(f"✅ Gemini 成功過濾出 {len(academic_refs_list)} 條學術引用，準備精確解析。")


    except json.JSONDecodeError:
        return None, f"Gemini 過濾 (階段 1.5) 返回了無效的 JSON 格式。原始回應：\n{response1_5.text[:200]}..."
    except Exception as e:
        return None, f"Gemini 呼叫失敗 (階段 1.5): {e}"


    # --- 階段 2：解析學術參考文獻 (使用區塊化) ---
    
    # 1. 將過濾後的學術文本分割成區塊
    chunks = chunk_reference_text(academic_refs_text, MAX_REF_CHUNK_SIZE)
    if not chunks:
        return [], "學術參考文獻區段內容為空。"

    all_parsed_refs = []
    debug_info_parts = []
    
    # 必須再次設定 JSON Mime Type，因為模型狀態不會跨呼叫保留
    config_json = {"response_mime_type": "application/json"}
    
    for i, chunk in enumerate(chunks):
        try:
            # 2. 針對每個區塊呼叫 Gemini
            prompt2 = PROMPT_TASK_2_PARSE.format(reference_text=chunk)
            response2 = model.generate_content(prompt2, generation_config=config_json)
            
            # 移除 JSON 外的 markdown 標記 (```json ... ```)
            clean_json_text = re.sub(r'```json\n(.*?)\n```', r'\1', response2.text, flags=re.DOTALL)
            
            # 3. 解析並合併結果
            parsed_refs_chunk = json.loads(clean_json_text)
            
            if isinstance(parsed_refs_chunk, list):
                # [重要] 由於 Task 1.5 已經過濾，這裡補上 is_academic=True
                for ref in parsed_refs_chunk:
                    ref["is_academic"] = True
                all_parsed_refs.extend(parsed_refs_chunk)
            else:
                debug_info_parts.append(f"區塊 {i+1}: 返回了非列表 JSON ({type(parsed_refs_chunk)})")

        except json.JSONDecodeError:
            debug_info_parts.append(f"區塊 {i+1}: 返回了無效的 JSON 格式。原始回應：\n{response2.text[:200]}...")
            continue
        except Exception as e:
            debug_info_parts.append(f"區塊 {i+1}: Gemini 呼叫失敗: {e}")
            continue

    if all_parsed_refs:
        return all_parsed_refs, "解析成功"
    else:
        return [], f"Gemini 返回了空的或無效的 JSON 列表。除錯資訊：{' | '.join(debug_info_parts)}"