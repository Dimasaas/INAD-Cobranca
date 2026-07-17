import re
import json

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Split by sales. The sales start with "Venda:            " or "Venda: "
# Let's split by "\n\s*Venda:\s+"
parts = re.split(r'\n\s*Venda:\s+', text)
blocks = parts[1:]

print(f"Total Venda blocks: {len(blocks)}")

clients = {}

for idx, block in enumerate(blocks):
    # Split block lines
    lines = [line for line in block.split("\n") if line.strip()]
    if not lines:
        continue
        
    # The first line contains Venda details
    # e.g., "20              Data da Venda:    09/07/2018      Total da Venda:                   147.420,00     Personalização:    411      Identificador:  QUADRA 15 LOTE 12                     Status da Cobrança:      SEM STATUS"
    first_line = lines[0]
    
    # Extract Venda ID
    venda_id_match = re.match(r"^\s*(\d+)", first_line)
    if not venda_id_match:
        continue
    venda_id = venda_id_match.group(1)
    
    # Extract Identificador (Quadra/Lote)
    ident_match = re.search(r"Identificador:\s*(.*?)(?:\s{2,}|Status|$)", first_line)
    identifier = ident_match.group(1).strip() if ident_match else ""
    
    # Let's find the Cliente line
    cliente_line = ""
    for line in lines[1:4]: # usually the line immediately following Venda details
        if "Cliente:" in line:
            cliente_line = line
            break
            
    if not cliente_line:
        # Check all lines in block
        for line in lines:
            if "Cliente:" in line:
                cliente_line = line
                break
                
    if not cliente_line:
        continue
        
    # Extract Client details using regex
    # e.g. Cliente:          NAYUME PEREIRA DEMESIO                                                CPF/CNPJ:     037.586.411-36               Res.:                      Com.:                         Cel.:  62 99239-1933       E-mail:  nayumedemesio@gmail.com
    name_match = re.search(r"Cliente:\s*(.*?)(?:\s{2,}|CPF/CNPJ|$)", cliente_line)
    cpf_match = re.search(r"CPF/CNPJ:\s*([^\s]+)", cliente_line)
    cel_match = re.search(r"Cel\.:\s*([^\s]+(?:\s+[^\s]+)*?)(?:\s{2,}|E-mail|$)", cliente_line)
    email_match = re.search(r"E-mail:\s*([^\s]+)", cliente_line)
    
    client_name = name_match.group(1).strip() if name_match else ""
    cpf_cnpj = cpf_match.group(1).strip() if cpf_match else ""
    cel = cel_match.group(1).strip() if cel_match else ""
    email = email_match.group(1).strip() if email_match else ""
    
    # Clean email (sometimes it has a trailing comma or E-mail label if parsed weirdly)
    email = re.sub(r"E-mail:.*$", "", email).strip()
    
    # Extract parcels
    parcels = []
    for line in lines:
        # Match parcel line:
        # e.g. "   90/180         P    15/06/2026       15/06/2026         32                  1.631,77 ..."
        # format: parcel_num, type, vencimento, prorrogação, atraso
        p_match = re.search(r"^\s*(\d+/\d+)\s+([A-Z])\s+(\d\d/\d\d/\d{4})", line)
        if p_match:
            parcel_num = p_match.group(1)
            vencimento = p_match.group(3)
            
            # Extract short date: "15/06/2026" -> "15/06"
            venc_short = vencimento[:5]
            
            parcels.append({
                "parcela": parcel_num,
                "vencimento": venc_short,
                "vencimento_full": vencimento
            })
            
    # Clean name
    client_name = re.sub(r"\s+", " ", client_name).strip()
    
    # Add to clients dict
    if client_name:
        if client_name not in clients:
            clients[client_name] = {
                "name": client_name,
                "cpf_cnpj": cpf_cnpj,
                "cel": cel,
                "email": email,
                "properties": []
            }
        # Add property
        clients[client_name]["properties"].append({
            "venda_id": venda_id,
            "identifier": identifier,
            "parcels": parcels
        })

print(f"Total unique clients parsed: {len(clients)}")

# print a few to verify
for i, (name, data) in enumerate(list(clients.items())[:5]):
    print(f"\nClient {i+1}: {name}")
    print(f"  Cel: {data['cel']}, Email: {data['email']}, CPF: {data['cpf_cnpj']}")
    for prop in data["properties"]:
        print(f"  Property: {prop['identifier']}, Parcels: {prop['parcels']}")

with open("clients_data.json", "w", encoding="utf-8") as f:
    json.dump(clients, f, ensure_ascii=False, indent=2)
print("Saved clean data to clients_data.json")
