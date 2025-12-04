import fitz

def extract_paragraphs(pdf_path):
    text = ""

    with fitz.open(pdf_path) as doc:
        for page in doc:
            text += page.get_text("text") + "\n"

    # 最原始的切法：只用換行分割
    paragraphs = [p.strip() for p in text.split("\n") if p.strip()]
    return paragraphs


if __name__ == "__main__":
    pdf_path = "QoE_Models_for_Virtual_Reality_Cloud-based_First_Person_Shooter_Game_over_Mobile_Networks(1).pdf"

    paragraphs = extract_paragraphs(pdf_path)

    # ✔ 完整印出最原始的陣列，不做預覽、不寫檔、不加工
    print(paragraphs)
