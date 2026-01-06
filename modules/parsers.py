import re
import unicodedata
import subprocess
import json
import streamlit as st
import tempfile
import os

def parse_references_with_anystyle(raw_text):
    if not raw_text or not raw_text.strip():
        return [], []

    # 🕵️ 雲端指令偵測邏輯
    # 嘗試所有可能的指令組合
    found_cmd = None
    test_cmds = [["anystyle", "--version"], ["ruby", "-S", "anystyle", "--version"]]
    
    for cmd in test_cmds:
        try:
            subprocess.run(cmd, capture_output=True, check=True)
            found_cmd = cmd[:-1] # 移除 --version
            break
        except:
            continue

    if not found_cmd:
        st.error("❌ 無法啟動解析引擎 (AnyStyle)。請嘗試 Manage App -> Reboot。")
        return [], []

    lines = [line.strip() for line in raw_text.split('\n') if line.strip()]
    structured_refs = []
    raw_texts = []
    
    progress_bar = st.progress(0)
    
    for i, line in enumerate(lines):
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', line))
        
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as tmp:
            tmp.write(line)
            tmp_path = tmp.name

        # 組合解析指令
        command = found_cmd + ["-f", "json", "parse"]
        if has_chinese and os.path.exists("custom.mod"):
            command += ["-P", "custom.mod"]
        command.append(tmp_path)

        try:
            result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", check=True)
            stdout = result.stdout.strip()
            
            # JSON 提取
            if "[" in stdout:
                stdout = stdout[stdout.find("[") : stdout.rfind("]")+1]
                data = json.loads(stdout)
                for item in data:
                    # 簡化作者格式
                    if 'author' in item:
                        authors = []
                        for a in item['author']:
                            authors.append(f"{a.get('family', '')} {a.get('given', '')}".strip())
                        item['authors'] = "; ".join(authors)
                    
                    if 'text' not in item: item['text'] = line
                    structured_refs.append(item)
                    raw_texts.append(line)
        except Exception as e:
            st.warning(f"第 {i+1} 筆解析失敗: {str(e)}")
        finally:
            os.remove(tmp_path)
        
        progress_bar.progress((i + 1) / len(lines))
    
    return raw_texts, structured_refs
# ==============================================================================
# 標題清洗函式
# ==============================================================================

def clean_title(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    dash_chars = ["-", "–", "—", "−", "‐", "-"]
    for d in dash_chars:
        text = text.replace(d, "")
    cleaned = [
        ch.lower()
        for ch in text
        if unicodedata.category(ch)[0] in ("L", "N", "Z")
    ]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()

def clean_title_for_remedial(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))
    dash_chars = ["-", "–", "—", "−", "‐", "-"]
    for d in dash_chars:
        text = text.replace(d, "")
    text = re.sub(r"\b\d+\b", "", text)
    cleaned = [
        ch.lower()
        for ch in text
        if unicodedata.category(ch)[0] in ("L", "N", "Z")
    ]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()


