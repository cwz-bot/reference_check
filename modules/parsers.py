import re
import unicodedata
import subprocess
import json
import streamlit as st
import tempfile
import os
import shutil

# ==============================================================================
# AnyStyle 解析（雲端/地端自動相容版）
# ==============================================================================

def get_ruby_command():
    """
    自動判定環境選擇正確的 ruby 呼叫方式
    1. 優先檢查系統 PATH (適用於 Linux/Streamlit Cloud)
    2. 若找不到則嘗試 Windows 預設安裝路徑
    """
    # 檢查系統環境變數中是否有 ruby 指令 (Linux 伺服器通常在此)
    ruby_in_path = shutil.which("ruby")
    if ruby_in_path:
        return "ruby"
    
    # Windows 預設路徑備案 (給地端同學使用)
    win_default_path = r"C:\Ruby34\bin\ruby.exe"
    if os.name == 'nt' and os.path.exists(win_default_path):
        return win_default_path
        
    return "ruby" # 預設回傳 ruby

# 設定全域指令變數
RUBY_EXE = get_ruby_command()

def parse_references_with_anystyle(raw_text_for_anystyle):
    """
    將文獻列表拆分處理：
    1. 含有中文字元：使用自定義模型 (-P custom.mod)
    2. 純英文：使用 AnyStyle 內建預設模型
    """
    if not raw_text_for_anystyle or not raw_text_for_anystyle.strip():
        return [], []

    # 【修正點】檢查系統是否找得到 ruby 指令，而不是單純檢查檔案路徑
    if shutil.which(RUBY_EXE) is None and not os.path.exists(RUBY_EXE):
        st.error(f"❌ 系統環境中找不到 Ruby。請確認 packages.txt 包含 ruby-full 且已 Reboot App。")
        return [], []

    # 將輸入文字按行拆分，過濾掉空行
    lines = [line.strip() for line in raw_text_for_anystyle.split('\n') if line.strip()]
    
    structured_refs = []
    raw_texts = []

    # 建立進度條
    progress_bar = st.progress(0)
    total_lines = len(lines)

    for i, line in enumerate(lines):
        # 語言判定
        has_chinese = bool(re.search(r'[\u4e00-\u9fff]', line))

        # 建立暫存檔
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".txt",
                delete=False,
                encoding="utf-8"
            ) as tmp:
                tmp.write(line)
                tmp_path = tmp.name
        except Exception as e:
            st.error(f"❌ 無法建立暫存檔：{e}")
            continue

        # 組合指令
        command = [
            RUBY_EXE,
            "-S",
            "anystyle",
            "-f", "json",
            "parse"
        ]

        # 偵測到中文且有自定義模型時使用 -P
        if has_chinese and os.path.exists("custom.mod"):
            command.insert(3, "-P")
            command.insert(4, "custom.mod")
        
        command.append(tmp_path)

        try:
            process = subprocess.run(
                command,
                capture_output=True,
                text=True,
                encoding="utf-8",
                check=True
            )

            stdout = process.stdout.strip()

            # 擷取 JSON 內容
            if not stdout.startswith("["):
                match = re.search(r"\[.*\]", stdout, re.DOTALL)
                if match:
                    stdout = match.group(0)

            line_data = json.loads(stdout)

            for item in line_data:
                cleaned_item = {}
                for key, value in item.items():
                    if isinstance(value, list):
                        if key == "author":
                            authors = []
                            for a in value:
                                if isinstance(a, dict):
                                    parts = [p for p in [a.get("given"), a.get("family")] if p]
                                    authors.append(" ".join(parts))
                                else:
                                    authors.append(str(a))
                            cleaned_item["authors"] = ", ".join(authors)
                        else:
                            cleaned_item[key] = " ".join(map(str, value))
                    else:
                        cleaned_item[key] = value

                if "text" not in cleaned_item:
                    cleaned_item["text"] = line

                structured_refs.append(cleaned_item)
                raw_texts.append(cleaned_item["text"])

        except Exception as e:
            st.error(f"解析第 {i+1} 行時發生錯誤：{e}")
        finally:
            # 刪除暫存檔
            try:
                if 'tmp_path' in locals():
                    os.remove(tmp_path)
            except:
                pass
        
        progress_bar.progress((i + 1) / total_lines)

    return raw_texts, structured_refs

# ==============================================================================
# 標題清洗函式 (保持原樣)
# ==============================================================================

def clean_title(text):
    if not text: return ""
    text = unicodedata.normalize("NFKC", str(text))
    dash_chars = ["-", "–", "—", "−", "‐", "-"]
    for d in dash_chars:
        text = text.replace(d, "")
    cleaned = [ch.lower() for ch in text if unicodedata.category(ch)[0] in ("L", "N", "Z")]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()

def clean_title_for_remedial(text):
    if not text: return ""
    text = unicodedata.normalize("NFKC", str(text))
    dash_chars = ["-", "–", "—", "−", "‐", "-"]
    for d in dash_chars:
        text = text.replace(d, "")
    text = re.sub(r"\b\d+\b", "", text)
    cleaned = [ch.lower() for ch in text if unicodedata.category(ch)[0] in ("L", "N", "Z")]
    return re.sub(r"\s+", " ", "".join(cleaned)).strip()
