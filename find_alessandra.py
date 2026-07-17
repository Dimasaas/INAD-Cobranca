with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

idx = text.find("***PII REMOVIDO***")
if idx != -1:
    print(text[idx:idx+1000])
else:
    print("Not found")
