# modules/parsers.py
import re
import unicodedata
import subprocess
import json
import streamlit as st
import tempfile
import os

# ==============================================================================
# AnyStyle 解析（Windows 強制指定 ruby.exe 絕對路徑版）
# ==============================================================================

# 🔴 你提供的 ruby.exe 絕對路徑（寫死）
RUBY_EXE = r"C:\Ruby34\bin\ruby.exe"


def parse_references_with_anystyle(raw_text_for_anystyle):
    if not raw_text_for_anystyle or not raw_text_for_anystyle.strip():
        return [], []

    # 1️⃣ 確認 ruby.exe 存在
    if not os.path.exists(RUBY_EXE):
        st.error(f"❌ 找不到 ruby.exe：{RUBY_EXE}")
        return [], []

    # 2️⃣ 寫入暫存檔（Windows 必須）
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            suffix=".txt",
            delete=False,
            encoding="utf-8"
        ) as tmp:
            tmp.write(raw_text_for_anystyle)
            tmp_path = tmp.name
    except Exception as e:
        st.error(f"❌ 無法建立暫存檔：{e}")
        return [], []

    # 3️⃣ 組合指令（明確指定 ruby.exe）
    # command = [
    #     RUBY_EXE,
    #     "-S",
    #     "anystyle",
    #     "--stdout",
    #     "-f", "json",
    #     "parse",
    #     tmp_path
    # ]
    # 中文 model
    command = [
        RUBY_EXE,
        "-S",
        "anystyle",
        "-P", "custom.mod",
        "-f", "json",
        "parse",
        tmp_path
    ]

    try:
        process = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True
        )

        stdout = process.stdout.strip()

        # 4️⃣ 擷取 JSON（避免混入 log）
        if not stdout.startswith("["):
            match = re.search(r"\[.*\]", stdout, re.DOTALL)
            if match:
                stdout = match.group(0)

        raw_data = json.loads(stdout)

        structured_refs = []
        raw_texts = []

        for item in raw_data:
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

            # fallback text
            if "text" not in cleaned_item:
                parts = []
                if "authors" in cleaned_item:
                    parts.append(cleaned_item["authors"])
                if "date" in cleaned_item:
                    parts.append(f"({cleaned_item['date']})")
                if "title" in cleaned_item:
                    parts.append(cleaned_item["title"])
                cleaned_item["text"] = ". ".join(parts) if parts else "Parsed Reference"

            structured_refs.append(cleaned_item)
            raw_texts.append(cleaned_item["text"])

        return raw_texts, structured_refs

    except subprocess.CalledProcessError as e:
        st.error("❌ AnyStyle CLI 執行失敗")
        st.code(e.stderr or e.stdout)
        return [], []

    except Exception as e:
        st.error(f"❌ AnyStyle 發生未知錯誤：{e}")
        return [], []

    finally:
        try:
            os.remove(tmp_path)
        except Exception:
            pass


# ==============================================================================
# 標題清洗（保持原樣）
# ==============================================================================

def clean_title(text):
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", str(text))

    # ✅ 安全移除各種 dash（不用 regex range）
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
