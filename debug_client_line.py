import re

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

parts = re.split(r'(?<!Data da )Venda:\s*', text)
block = parts[1]

# Print line by line
for line in block.split("\n"):
    if "Cliente:" in line:
        print("Line:", repr(line))
        
        # Test match on this single line
        m = re.search(r"(.*?)Cliente:\s*(.*?)CPF/CNPJ:", line)
        if m:
            print("Match found on single line:")
            print("  Name:", repr(m.group(1).strip()))
            print("  CPF:", repr(m.group(2).strip()))
        else:
            print("No match on single line")
