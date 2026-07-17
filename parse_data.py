import re
import json

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Let's split by "Venda:" to get each sale block
# We exclude the first split because it will be the header of Page 1
parts = text.split("Venda:")
header = parts[0]
blocks = parts[1:]

print(f"Total Venda blocks found: {len(blocks)}")

clients = {}

for idx, block in enumerate(blocks):
    # Let's extract Venda ID
    venda_match = re.match(r"^\s*(\d+)", block)
    if not venda_match:
        continue
    venda_id = venda_match.group(1)
    
    # Let's find the Identifier (Quadra/Lote)
    # The identifier format is: "Identificador: <Ident_value> Status da Cobrança" or similar
    ident_match = re.search(r"Identificador:\s*(.*?)\s*(?:Status da Cobrança|Total da Venda|$)", block, re.DOTALL)
    identifier = ident_match.group(1).strip() if ident_match else ""
    # Clean up identifier
    identifier = re.sub(r"\s+", " ", identifier)
    
    # Let's find the Cliente Name, CPF, Phone, Email
    # Format: <Name>Cliente: <CPF>CPF/CNPJ: <Res>Res.: <Com>Com.: <Cel>Cel.: <Email>E-mail:
    # Or some variations
    client_match = re.search(r"([^\n]*?)Cliente:\s*([^\n]*?)CPF/CNPJ:\s*(.*?)(?:Res\.:|Com\.:|Cel\.:|E-mail\.:|E-mail:|$)", block, re.DOTALL)
    if not client_match:
        # try another regex
        client_match = re.search(r"Cliente:\s*([^\n]*?)CPF/CNPJ:\s*([^\n]*?)", block)
    
    if client_match:
        client_name = client_match.group(1).strip()
        cpf_cnpj = client_match.group(2).strip()
    else:
        client_name = ""
        cpf_cnpj = ""
    
    # Let's extract Phone (Cel.) and Email
    # In some blocks, we have: "Cel.: <Email>E-mail:" where phone was before Res.:
    # Let's search for "Cel.:" and "E-mail:"
    # In Wilson: "288.947.581-68CPF/CNPJ: 62 99408-1882Res.: Com.: Cel.: wilsondomingos05@gmail.comE-mail:"
    # Here, Cel number "62 99408-1882" is between CPF/CNPJ: and Res.:
    # Email "wilsondomingos05@gmail.com" is between Cel.: and E-mail:
    # Let's verify this pattern
    
    cel = ""
    email = ""
    
    # Phone number is usually after CPF/CNPJ: but before Res.: or Com.:
    # e.g., "CPF/CNPJ: 62 99408-1882Res.:"
    # Let's find all text between CPF/CNPJ: and Res.:
    phone_match1 = re.search(r"CPF/CNPJ:\s*(.*?)(?:Res\.:|Com\.:|Cel\.:|$)", block)
    if phone_match1:
        phone_candidate = phone_match1.group(1).strip()
        # Clean it up. It should contain digits (like a phone number)
        # e.g. "62 99408-1882"
        # We can extract the phone number from this
        phone_nums = re.findall(r"\b\d{2}\s*\d{4,5}[-\s]?\d{4}\b|\b\d{2}\s*\d{4,5}\b", phone_candidate)
        if phone_nums:
            cel = phone_nums[0]
            
    # If phone is not there, check Cel.:
    cel_match = re.search(r"Cel\.:\s*(.*?)(?:E-mail:|$)", block)
    if cel_match:
        cel_candidate = cel_match.group(1).strip()
        # check if it is a phone or email
        if "@" not in cel_candidate and not cel:
            phone_nums = re.findall(r"\b\d{2}\s*\d{4,5}[-\s]?\d{4}\b|\b\d{2}\s*\d{4,5}\b", cel_candidate)
            if phone_nums:
                cel = phone_nums[0]
            else:
                # keep as is if it looks like a phone
                cel = cel_candidate[:20]
    
    # Email is after Cel.: and before E-mail: or similar
    # e.g., "Cel.: wilsondomingos05@gmail.comE-mail:"
    # So it's between Cel.: and E-mail:
    email_match = re.search(r"Cel\.:\s*(.*?)(?:E-mail:|$)", block, re.DOTALL)
    if email_match:
        email_candidate = email_match.group(1).strip()
        if "@" in email_candidate:
            # Clean up. Email might end with some text or just be an email address
            email_addr = re.search(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", email_candidate)
            if email_addr:
                email = email_addr.group(0)
    
    if not email:
        # try another regex
        email_match2 = re.search(r"E-mail:\s*([^\s\n]+)", block)
        if email_match2:
            email = email_match2.group(1).strip()
            
    # Clean client name if it has garbage at the start (sometimes page headers get caught)
    # e.g. "Total da Venda: 150.800,80\nWILSON DOMINGOS DA SILVA"
    client_name = re.sub(r"Total da Venda:.*?\n", "", client_name, flags=re.DOTALL)
    client_name = re.sub(r"^.*?\bCliente\b", "", client_name) # clean up
    client_name = client_name.strip()
    
    # Let's extract all parcels
    # A parcel line usually contains:
    # "P \d\d/\d\d/\d{4} \d\d/\d\d/\d{4}" or "\d+/\d+ P \d\d/\d\d/\d{4}"
    # Let's find all instances of parcel numbers like 90/180, 93/180, etc.
    # And their vencimento date.
    
    parcels = []
    lines = block.split("\n")
    for line in lines:
        # Check if line contains a parcel number (\d+/\d+)
        p_match = re.search(r"(\d+/\d+)", line)
        if p_match:
            parcel_num = p_match.group(1)
            # Find dates in the same line
            dates = re.findall(r"(\d\d/\d\d/\d{4})", line)
            # We want the Vencimento date.
            # In clean lines, Vencimento is the first date.
            # Let's see: if there are dates, we take the first one.
            if dates:
                vencimento = dates[0]
                parcels.append({
                    "parcela": parcel_num,
                    "vencimento": vencimento
                })
                
    if client_name:
        # Let's group by client name
        # A client can have multiple sales/properties!
        if client_name not in clients:
            clients[client_name] = {
                "name": client_name,
                "cpf_cnpj": cpf_cnpj,
                "cel": cel,
                "email": email,
                "properties": []
            }
        
        # Add property details
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
        
# Write to JSON file
with open("clients.json", "w", encoding="utf-8") as f:
    json.dump(clients, f, ensure_ascii=False, indent=2)
print("Saved parsed clients to clients.json")
