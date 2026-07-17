import re

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Split using negative lookbehinds for both "Data da Venda:" and "Total da Venda:"
parts = re.split(r'(?<!Data da )(?<!Total da )Venda:\s*', text)

print(f"Total parts split: {len(parts)}")
print("\n--- PART 1 START ---")
print(parts[1][:800])
print("--- PART 1 END ---")
