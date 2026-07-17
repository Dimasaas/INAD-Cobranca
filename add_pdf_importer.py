import json
import os

# Load the current clients data
if not os.path.exists("clients_data.json"):
    print("Error: clients_data.json not found!")
    exit(1)

with open("clients_data.json", "r", encoding="utf-8") as f:
    clients = json.load(f)

# Fix any leftover bad values in phone/cel
bad_cel_values = {"E-mail:", "351"}
for name, c in clients.items():
    if c.get("cel", "") in bad_cel_values:
        c["cel"] = ""

clients_js = json.dumps(clients, ensure_ascii=False, separators=(',', ':'))

# Read the HTML template
if not os.path.exists("inad_template.html"):
    print("Error: inad_template.html not found! Run from the correct directory.")
    exit(1)

with open("inad_template.html", "r", encoding="utf-8") as f:
    html_template = f.read()

# Replace the CLIENTS_JSON_PLACEHOLDER in the HTML
html_out = html_template.replace("CLIENTS_JSON_PLACEHOLDER", clients_js)

# Write the final inad_whatsapp.html
with open("inad_whatsapp.html", "w", encoding="utf-8") as f:
    f.write(html_out)

print(f"Successfully generated inad_whatsapp.html with client-side PDF parser! Size: {len(html_out):,} bytes")
