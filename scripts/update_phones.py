import re
import json

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Split by sales
parts = re.split(r'\n\s*Venda:\s+', text)
blocks = parts[1:]

with open("clients_data.json", "r", encoding="utf-8") as f:
    clients = json.load(f)

# Helper to find any phone numbers in a string
def extract_phones(s):
    # Match DDD + 8 or 9 digits, or just 8 or 9 digits if DDD is separate
    # e.g., 62 99239-1933, 62992391933, 62 3542-3759, 737720-1229
    # We clean up non-digits except space and hyphen first, then extract
    # Find all sequences of numbers that look like phones
    # Let's search for patterns:
    # 1. \d{2}\s*9\d{4}[-\s]?\d{4} (DDD + cell)
    # 2. \d{2}\s*\d{4}[-\s]?\d{4} (DDD + landline)
    # 3. \d{4,5}[-\s]?\d{4} (phone without DDD)
    candidates = []
    
    # Clean up string
    s_clean = re.sub(r'\s+', ' ', s)
    
    # 1. Find full phones with DDD
    matches = re.findall(r'\b\d{2}\s*9\d{4}[-\s]?\d{4}\b|\b\d{2}\s*\d{4}[-\s]?\d{4}\b', s_clean)
    for m in matches:
        candidates.append(m)
        
    # 2. Find phones without DDD (8 or 9 digits)
    matches_no_ddd = re.findall(r'\b9\d{4}[-\s]?\d{4}\b|\b\d{4}[-\s]?\d{4}\b', s_clean)
    for m in matches_no_ddd:
        candidates.append(m)
        
    # Clean non-digits
    cleaned = []
    for c in candidates:
        digits = re.sub(r'\D', '', c)
        if len(digits) >= 8:
            cleaned.append(digits)
            
    return cleaned

print("Re-parsing phones for all clients...")

for block in blocks:
    # Get lines
    lines = [line for line in block.split("\n") if line.strip()]
    if not lines:
        continue
        
    # Find the Cliente line and the line below it
    cliente_idx = -1
    for i, line in enumerate(lines):
        if "Cliente:" in line:
            cliente_idx = i
            break
            
    if cliente_idx == -1:
        continue
        
    cliente_line = lines[cliente_idx]
    # Check if there is a line below that might contain part of the phone/email
    next_line = lines[cliente_idx+1] if cliente_idx+1 < len(lines) else ""
    if next_line and ("Parc." in next_line or "Vencimento" in next_line or "Venda" in next_line):
        next_line = "" # don't include headers
        
    # Combine lines for parsing
    combined_line = cliente_line + " " + next_line
    
    # Find client name
    name_match = re.search(r"Cliente:\s*(.*?)(?:\s{2,}|CPF/CNPJ|$)", combined_line)
    if not name_match:
        continue
    client_name = re.sub(r"\s+", " ", name_match.group(1)).strip()
    
    if client_name not in clients:
        continue
        
    # Let's extract values from Res., Com., Cel. columns specifically
    # In the combined line, find Res.:, Com.:, Cel.:, E-mail: positions
    res_pos = combined_line.find("Res.:")
    com_pos = combined_line.find("Com.:")
    cel_pos = combined_line.find("Cel.:")
    email_pos = combined_line.find("E-mail:")
    
    res_text = ""
    com_text = ""
    cel_text = ""
    
    # Extract segments
    if res_pos != -1:
        end = com_pos if com_pos != -1 else (cel_pos if cel_pos != -1 else (email_pos if email_pos != -1 else len(combined_line)))
        res_text = combined_line[res_pos:end]
    if com_pos != -1:
        end = cel_pos if cel_pos != -1 else (email_pos if email_pos != -1 else len(combined_line))
        com_text = combined_line[com_pos:end]
    if cel_pos != -1:
        end = email_pos if email_pos != -1 else len(combined_line)
        cel_text = combined_line[cel_pos:end]
        
    # Gather all phone candidates from these columns
    phone_candidates = []
    
    # Extract digits and check if they look like phones
    # We also handle numbers split across lines (like Cel: 351 \n 93586-2517)
    # If the column has only a few digits and the next line has the rest, we combine them
    for col_name, col_text in [("Res", res_text), ("Com", com_text), ("Cel", cel_text)]:
        # Remove the column label
        val = re.sub(r'^(Res\.:|Com\.:|Cel\.:)', '', col_text).strip()
        if val:
            # Check if it has phone numbers
            nums = extract_phones(val)
            for n in nums:
                phone_candidates.append((col_name, n))
            # Also check if it has a raw number (without spaces/hyphens)
            digits = re.sub(r'\D', '', val)
            if len(digits) >= 8:
                phone_candidates.append((col_name, digits))
                
    # Select the best phone number
    best_phone = ""
    best_source = ""
    
    # 1. Prefer mobile numbers with DDD (11 digits: starts with 9 after 2-digit DDD, e.g., 62992391933)
    for src, p in phone_candidates:
        if len(p) == 11 and p[2] == '9':
            best_phone = p
            best_source = src
            break
            
    # 2. Prefer any 11-digit number
    if not best_phone:
        for src, p in phone_candidates:
            if len(p) == 11:
                best_phone = p
                best_source = src
                break
                
    # 3. Prefer any 10-digit number (DDD + 8-digit landline or old mobile)
    if not best_phone:
        for src, p in phone_candidates:
            if len(p) == 10:
                best_phone = p
                best_source = src
                break
                
    # 4. Fallback to any number >= 8 digits
    if not best_phone and phone_candidates:
        # Sort by length descending
        phone_candidates.sort(key=lambda x: len(x[1]), reverse=True)
        best_phone = phone_candidates[0][1]
        best_source = phone_candidates[0][0]

    # Format phone number for readability: (DD) 9XXXX-XXXX or (DD) XXXX-XXXX
    formatted_phone = ""
    if best_phone:
        # Clean DDD
        if len(best_phone) == 11:
            formatted_phone = f"{best_phone[:2]} {best_phone[2:7]}-{best_phone[7:]}"
        elif len(best_phone) == 10:
            formatted_phone = f"{best_phone[:2]} {best_phone[2:6]}-{best_phone[6:]}"
        else:
            formatted_phone = best_phone
            
    old_cel = clients[client_name].get("cel", "")
    if formatted_phone != old_cel:
        print(f"Updated {client_name}: {old_cel!r} -> {formatted_phone!r} (from {best_source})")
        clients[client_name]["cel"] = formatted_phone

with open("clients_data.json", "w", encoding="utf-8") as f:
    json.dump(clients, f, ensure_ascii=False, indent=2)

print("Saved updated clients_data.json.")
