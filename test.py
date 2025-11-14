# check_models.py
import google.generativeai as genai
import os

# --- [設定] 請手動設定您的金鑰 ---
# 為了方便測試，您可以直接貼上金鑰，或者像下面這樣讀取 secrets
# (請確保您的 .streamlit/secrets.toml 檔案存在)

try:
    # 嘗試從 Streamlit secrets 讀取
    import toml
    secrets = toml.load(".streamlit/secrets.toml")
    API_KEY = secrets["gemini_api_key"]
except Exception:
    print("未能在 .streamlit/secrets.toml 找到金鑰。")
    # 或者，您可以暫時直接在這裡貼上金鑰來測試：
    # API_KEY = "YOUR_GEMINI_API_KEY_HERE" 
    API_KEY = None

if not API_KEY:
    print("請設定 API_KEY 變數！")
else:
    genai.configure(api_key=API_KEY)

    print("正在查詢您的帳戶可用的模型...\n")

    try:
        # 迭代所有可用的模型
        for model in genai.list_models():
            # 我們只關心支援 'generateContent' (也就是聊天/生成) 的模型
            if 'generateContent' in model.supported_generation_methods:
                
                print("="*30)
                print(f"✅ 模型名稱 (Model Name): {model.name}")
                print(f"   - 說明: {model.description}")
                print(f"   - 支援方法: {model.supported_generation_methods}")

    except Exception as e:
        print(f"\n--- 查詢失敗 ---")
        print(f"發生錯誤：{e}")
        print("請檢查您的 API 金鑰是否正確，以及網路連線是否正常。")