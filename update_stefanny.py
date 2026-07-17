import json
import re

# 1. Load clients data
with open("clients_data.json", "r", encoding="utf-8") as f:
    clients = json.load(f)

# Update Stefanny's phone to the correct format
# The user provided: 62982877839
phone_input = "62982877839"
formatted_phone = f"{phone_input[:2]} {phone_input[2:7]}-{phone_input[7:]}" # "***PII REMOVIDO***"

if "***PII REMOVIDO***" in clients:
    old_phone = clients["***PII REMOVIDO***"].get("cel", "")
    clients["***PII REMOVIDO***"]["cel"] = formatted_phone
    print(f"Updated ***PII REMOVIDO*** phone: {old_phone!r} -> {formatted_phone!r}")
else:
    # Try case-insensitive search if needed
    found = False
    for name in clients:
        if name.upper() == "***PII REMOVIDO***":
            old_phone = clients[name].get("cel", "")
            clients[name]["cel"] = formatted_phone
            print(f"Updated {name} phone: {old_phone!r} -> {formatted_phone!r}")
            found = True
            break
    if not found:
        print("Error: Client ***PII REMOVIDO*** not found in database!")

clients_js = json.dumps(clients, ensure_ascii=False, separators=(',', ':'))

# 2. Read existing inad_whatsapp.html
with open("inad_whatsapp.html", "r", encoding="utf-8") as f:
    html = f.read()

# Update the JSON placeholder DATA constant
html = re.sub(r'const DATA=.*?;', f'const DATA={clients_js};', html)

with open("inad_whatsapp.html", "w", encoding="utf-8") as f:
    f.write(html)

print("Saved updated inad_whatsapp.html.")
