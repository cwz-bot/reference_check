# modules/parsers.py

import re
import unicodedata
import subprocess
import json
import streamlit as st

# ==============================================================================
#                 [ AnyStyle 解析功能 (Docker 版 - 修正路徑) ]
# ==============================================================================
def parse_references_with_anystyle(raw_text_for_anystyle):
    """
    呼叫 Docker 容器內的 AnyStyle CLI 來解析參考文獻。
    """
    if not raw_text_for_anystyle or not raw_text_for_anystyle.strip():
        return [], []

    try:
        # 🐳 修正重點：將 '-' 改為 '/dev/stdin'
        # AnyStyle 不支援 '-' 符號，但支援 Linux 的標準輸入裝置檔案路徑
        command = ['docker', 'run', '--rm', '-i', 'anystyle-local', '--stdout', '-f', 'json', 'parse', '/dev/stdin']
        
        # 呼叫 Docker
        process = subprocess.run(
            command,
            input=raw_text_for_anystyle, # 透過這裡傳送文字給 /dev/stdin
            capture_output=True,
            text=True, 
            encoding='utf-8', 
            check=True
        )
        
        # --- 解析 JSON 輸出 ---
        try:
            # 有時候 Docker 會在 stdout 混雜一些非 JSON 的 Log，這裡做個簡單擷取
            json_str = process.stdout.strip()
            # 如果開頭不是 [，嘗試用正則表達式抓取 JSON 陣列
            if not json_str.startswith('['):
                match = re.search(r'\[.*\]', json_str, re.DOTALL)
                if match:
                    json_str = match.group(0)
            
            raw_data = json.loads(json_str)
            
        except json.JSONDecodeError:
            st.error("❌ AnyStyle 回傳的不是有效的 JSON。")
            st.code(process.stdout) # 顯示原始輸出以便除錯
            return [], []
        
        # --- 資料清洗與攤平 ---
        structured_refs = []
        raw_texts = []

        for item in raw_data:
            cleaned_item = {}
            for key, value in item.items():
                if isinstance(value, list):
                    # 作者欄位處理
                    if key == 'author':
                        authors_list = []
                        for auth in value:
                            if isinstance(auth, dict):
                                parts = [p for p in [auth.get('given'), auth.get('family')] if p]
                                authors_list.append(" ".join(parts))
                            else:
                                authors_list.append(str(auth))
                        cleaned_item['authors'] = ", ".join(authors_list)
                    # 其他欄位直接合併
                    else:
                        cleaned_item[key] = " ".join([str(v) for v in value])
                else:
                    cleaned_item[key] = value

            # 產生 text 欄位
            if 'text' not in cleaned_item:
                fallback_parts = []
                if 'authors' in cleaned_item: fallback_parts.append(cleaned_item['authors'])
                if 'date' in cleaned_item: fallback_parts.append(f"({cleaned_item['date']})")
                if 'title' in cleaned_item: fallback_parts.append(cleaned_item['title'])
                cleaned_item['text'] = ". ".join(fallback_parts) if fallback_parts else "Parsed Reference"

            structured_refs.append(cleaned_item)
            raw_texts.append(cleaned_item.get('text', ''))
        
        return raw_texts, structured_refs
        
    except subprocess.CalledProcessError as e:
        st.error("❌ Docker 執行失敗。")
        # 這裡會顯示具體的錯誤訊息，例如路徑錯誤等
        st.error(f"錯誤訊息 (Stderr): {e.stderr}")
        return [], []
    except FileNotFoundError:
        st.error("❌ 找不到 'docker' 指令。請確認 Docker Desktop 已啟動。")
        return [], []
    except Exception as e:
        st.error(f"❌ 發生錯誤: {e}")
        return [], []

# ==============================================================================
#                 [ 標題清洗輔助函式 (保持不變) ]
# ==============================================================================

def clean_title(text):
    if not text: return ""
    text = str(text)
    dash_variants = ["-", "–", "—", "−", "‑", "‐"]
    for d in dash_variants: text = text.replace(d, "")
    text = unicodedata.normalize('NFKC', text)
    cleaned = [ch.lower() for ch in text if unicodedata.category(ch)[0] in ("L", "N", "Z")]
    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()

def clean_title_for_remedial(text):
    if not text: return ""
    text = str(text)
    text = unicodedata.normalize('NFKC', text)
    text = re.sub(r'\b\d+\b', '', text) 
    cleaned = [ch.lower() for ch in text if unicodedata.category(ch)[0] in ("L", "N", "Z")]
    return re.sub(r'\s+', ' ', ''.join(cleaned)).strip()