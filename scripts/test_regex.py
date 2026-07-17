import re

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Split using negative lookbehind to avoid "Data da Venda:"
parts = re.split(r'(?<!Data da )Venda:\s*', text)

print(f"Total parts split: {len(parts)}")
print("\n--- PART 1 START ---")
print(parts[1][:600])
print("--- PART 1 END ---")

block = parts[1]

# Test identifier regex
ident_match = re.search(r"Identificador:\s*(.*?)\s*(?:Status|$)", block)
if ident_match:
    print("Ident Match:", ident_match.group(1).strip())
else:
    print("Ident Match: None")

# Test client name regex
client_match = re.search(r"([^\n]*?)Cliente:\s*([^\n]*?)CPF/CNPJ:", block)
if client_match:
    print("Client Name:", client_match.group(1).strip())
    print("CPF/CNPJ:", client_match.group(2).strip())
else:
    print("Client Match: None")
