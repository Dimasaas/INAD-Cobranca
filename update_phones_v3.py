import re
import json

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Split by sales
parts = re.split(r'\n\s*Venda:\s+', text)
blocks = parts[1:]

print(f"Total blocks to scan: {len(blocks)}")

# Load original parsed clients
with open("clients_data.json", "r", encoding="utf-8") as f:
    clients = json.load(f)

# Helper to clean phone numbers
def clean_digits(val):
    if not val:
        return ""
    # Extract only digits
    digits = re.sub(r'\D', '', val)
    return digits

def format_brazilian_phone(digits):
    if len(digits) == 11:
        return f"{digits[:2]} {digits[2:7]}-{digits[7:]}"
    elif len(digits) == 10:
        return f"{digits[:2]} {digits[2:6]}-{digits[6:]}"
    elif len(digits) == 9:
        return f"{digits[0]} {digits[1:5]}-{digits[5:]}"
    elif len(digits) == 8:
        return f"{digits[:4]}-{digits[4:]}"
    return digits

updated_count = 0

for block in blocks:
    lines = block.split("\n")
    if not lines:
        continue
        
    # Find the Cliente line index
    cliente_idx = -1
    for i, line in enumerate(lines):
        if "Cliente:" in line:
            cliente_idx = i
            break
            
    if cliente_idx == -1:
        continue
        
    cliente_line = lines[cliente_idx]
    
    # We want to extract column indices from the Cliente line
    client_pos = cliente_line.find("Cliente:")
    cpf_pos = cliente_line.find("CPF/CNPJ:")
    res_pos = cliente_line.find("Res.:")
    com_pos = cliente_line.find("Com.:")
    cel_pos = cliente_line.find("Cel.:")
    email_pos = cliente_line.find("E-mail:")
    
    # We define the ranges for each column
    col_ranges = {
        "name": (client_pos, cpf_pos if cpf_pos != -1 else 100),
        "cpf": (cpf_pos, res_pos if res_pos != -1 else 130),
        "res": (res_pos, com_pos if com_pos != -1 else 160),
        "com": (com_pos, cel_pos if cel_pos != -1 else 185),
        "cel": (cel_pos, email_pos if email_pos != -1 else 215),
        "email": (email_pos, len(cliente_line))
    }
    
    # We will gather the text for each column across multiple lines in the client section
    client_section_lines = []
    for line in lines[cliente_idx:]:
        if "Parc." in line or "Vencimento" in line or "Venda:" in line:
            break
        client_section_lines.append(line)
        
    col_values = {"name": "", "cpf": "", "res": "", "com": "", "cel": "", "email": ""}
    
    for col_name, (start, end) in col_ranges.items():
        if start == -1:
            continue
        segments = []
        for line in client_section_lines:
            if len(line) > start:
                segment = line[start:end].strip()
                if col_name == "name":
                    segment = re.sub(r"^Cliente:\s*", "", segment)
                elif col_name == "cpf":
                    segment = re.sub(r"^CPF/CNPJ:\s*", "", segment)
                elif col_name == "res":
                    segment = re.sub(r"^Res\.:\s*", "", segment)
                elif col_name == "com":
                    segment = re.sub(r"^Com\.:\s*", "", segment)
                elif col_name == "cel":
                    segment = re.sub(r"^Cel\.:\s*", "", segment)
                elif col_name == "email":
                    segment = re.sub(r"^E-mail:\s*", "", segment)
                if segment:
                    segments.append(segment)
        col_values[col_name] = " ".join(segments).strip()
        
    client_name = re.sub(r"\s+", " ", col_values["name"]).strip()
    if not client_name or client_name not in clients:
        continue
        
    res_raw = col_values["res"]
    com_raw = col_values["com"]
    cel_raw = col_values["cel"]
    
    res_digits = clean_digits(res_raw)
    com_digits = clean_digits(com_raw)
    cel_digits = clean_digits(cel_raw)
    
    # Special cleanups
    if len(cel_digits) == 12 and cel_digits.startswith("3519"):
        cel_digits = "35" + cel_digits[3:] # '35935862517'
        
    if len(res_digits) == 12 and res_digits.startswith("4473"):
        # Let's verify: 44 + 973772012 (or similar). If it ends with 1229:
        # Let's clean it up to '44 97377-2012' (using the first 9 digits: 44 97377 2012)
        res_digits = "44973772012"
        
    best_phone = ""
    source = ""
    
    # Prioritize Celular (cel) > Residencial (res) > Comercial (com)
    if len(cel_digits) >= 8 and "gmail" not in cel_raw and "@" not in cel_raw:
        best_phone = cel_digits
        source = "Cel"
    elif len(res_digits) >= 8 and "gmail" not in res_raw and "@" not in res_raw:
        best_phone = res_digits
        source = "Res"
    elif len(com_digits) >= 8 and "gmail" not in com_raw and "@" not in com_raw:
        best_phone = com_digits
        source = "Com"
        
    formatted = format_brazilian_phone(best_phone)
    
    old_phone = clients[client_name].get("cel", "")
    if formatted != old_phone:
        print(f"Update {client_name}: {old_phone!r} -> {formatted!r} (from {source})")
        clients[client_name]["cel"] = formatted
        updated_count += 1

# Apply overrides for manually verified numbers to prevent future parser overwrites
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"





# Save updated database
with open("clients_data.json", "w", encoding="utf-8") as f:
    json.dump(clients, f, ensure_ascii=False, indent=2)

print(f"Done! Updated {updated_count} client phone numbers.")
