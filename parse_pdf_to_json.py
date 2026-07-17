import re
import json
import os

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Split by sale blocks
parts = re.split(r'(?<!Data da )(?<!Total da )Venda:\s*', text)
blocks = parts[1:]

clients = {}

for block in blocks:
    # 1. Extract Venda ID
    venda_match = re.match(r"^\s*(\d+)", block)
    if not venda_match:
        continue
    venda_id = venda_match.group(1)
    
    # 2. Extract Identifier (Quadra/Lote)
    ident_match = re.search(r"Identificador:\s*(.*?)\s*(?:Status da Cobrança|Total da Venda|$)", block, re.DOTALL)
    identifier = ident_match.group(1).strip() if ident_match else ""
    identifier = re.sub(r"\s+", " ", identifier)
    
    # 3. Clean up block text for client info extraction (merge consecutive newlines and spaces)
    # We will search the first part of the block before the parcel table
    client_info_part = block.split("Parc. Tipo Vencimento")[0]
    client_info_clean = re.sub(r"\s+", " ", client_info_part).strip()
    
    # Extract Client Name, CPF/CNPJ, Cel, Email
    # Pattern: <Name>Cliente: <CPF>CPF/CNPJ: <Cel_or_Phone>Res.: Com.: Cel.: <Email>E-mail:
    # Sometimes Cel is empty or has a phone number.
    # Let's search for "Cliente:" and "CPF/CNPJ:"
    client_match = re.search(r"(.*?)Cliente:\s*(.*?)CPF/CNPJ:\s*(.*?)(?:Res\.:|Com\.:|Cel\.:|$)", client_info_clean)
    
    if client_match:
        client_name = client_match.group(1).strip()
        cpf_cnpj = client_match.group(2).strip()
        phone_section = client_match.group(3).strip()
    else:
        # Fallback if structure is slightly different
        client_name = ""
        cpf_cnpj = ""
        phone_section = ""
    
    # Clean client name from headers
    client_name = re.sub(r"Total da Venda:.*$", "", client_name).strip()
    # Remove any leading digits or page headers from name if present
    client_name = re.sub(r"^.*?\bCliente\b", "", client_name) # double safety
    client_name = client_name.strip()
    
    if not client_name:
        continue
        
    # Extract Cell Phone and Email from the cleaned string
    cel = ""
    email = ""
    
    # Let's search for phone numbers in the phone_section and the whole clean info
    # Brazilian cell phones: 2-digit DDD + 9-digit number, e.g., 62 99239-1933 or 62 992391933
    # Look for any number matching DDD + mobile prefix (usually 9)
    all_phones = re.findall(r"\b\d{2}\s*9\d{4}[-\s]?\d{4}\b|\b\d{2}\s*\d{4}[-\s]?\d{4}\b", client_info_clean)
    if all_phones:
        # We prefer a phone number starting with 9 (mobile)
        mobiles = [p for p in all_phones if re.search(r"\b\d{2}\s*9", p)]
        if mobiles:
            cel = mobiles[0]
        else:
            cel = all_phones[0]
            
    # Email extraction
    email_match = re.search(r"([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})", client_info_clean)
    if email_match:
        email = email_match.group(1).strip()
        
    # 4. Extract Parcels
    parcels = []
    lines = block.split("\n")
    for line in lines:
        # We need lines that have parcel indicators
        # First find all dates
        dates = re.findall(r"(\d\d/\d\d/\d{4})", line)
        if not dates:
            continue
        # Remove dates to isolate parcel numbers
        line_no_dates = re.sub(r"\d\d/\d\d/\d{4}", "", line)
        # Find parcel number (\d+/\d+)
        p_match = re.search(r"(\d+/\d+)", line_no_dates)
        if p_match:
            parcel_num = p_match.group(1)
            vencimento = dates[0] # Vencimento is always the first date on the line
            
            # Extract month/day from vencimento
            # e.g., "15/06/2026" -> "15/06"
            date_match = re.match(r"(\d\d/\d\d)", vencimento)
            venc_short = date_match.group(1) if date_match else vencimento
            
            parcels.append({
                "parcela": parcel_num,
                "vencimento": venc_short,
                "vencimento_full": vencimento
            })
            
    # 5. Group by Client
    # Let's clean the name further (remove double spaces)
    client_name = re.sub(r"\s+", " ", client_name).strip()
    
    if client_name not in clients:
        clients[client_name] = {
            "name": client_name,
            "cpf_cnpj": cpf_cnpj,
            "cel": cel,
            "email": email,
            "properties": []
        }
        
    # Append the property if not already added for this client
    # (some sales might have multiple entries, though usually 1 property per sale)
    clients[client_name]["properties"].append({
        "venda_id": venda_id,
        "identifier": identifier,
        "parcels": parcels
    })

# Save to JSON
with open("clients_data.json", "w", encoding="utf-8") as f:
    json.dump(clients, f, ensure_ascii=False, indent=2)

print(f"Extraction complete! Extracted {len(clients)} unique clients.")
