import re

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

parts = re.split(r'(?<!Data da )Venda:\s*', text)
block = parts[1]

print("--- FULL BLOCK 1 ---")
print(block)
print("--- END ---")
