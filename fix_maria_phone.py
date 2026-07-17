import json

# 1. Load clients data
with open("clients_data.json", "r", encoding="utf-8") as f:
    clients = json.load(f)

# Update Maria's phone to the correct US format
clients["***PII REMOVIDO***"]["cel"] = "***PII REMOVIDO***"
print("Updated Maria's phone in clients_data.json to ***PII REMOVIDO***")

clients_js = json.dumps(clients, ensure_ascii=False, separators=(',', ':'))

# 2. Read existing inad_whatsapp.html
with open("inad_whatsapp.html", "r", encoding="utf-8") as f:
    html = f.read()

# Update the JSON placeholder DATA constant
html = html.replace('const DATA=CLIENTS_JSON_PLACEHOLDER;', f'const DATA={clients_js};')
# If DATA was already replaced with actual JSON, let's use a regex to replace it
import re
html = re.sub(r'const DATA=.*?;', f'const DATA={clients_js};', html)

# 3. Update the waLink function in HTML
# Let's locate "function waLink(cel,msg)" and replace the function block
start_idx = html.find("function waLink(cel,msg)")
if start_idx != -1:
    # Find the matching closing bracket for the function
    # It starts with '{' and ends with '}'
    bracket_count = 0
    end_idx = -1
    for i in range(start_idx, len(html)):
        if html[i] == '{':
            bracket_count += 1
        elif html[i] == '}':
            bracket_count -= 1
            if bracket_count == 0:
                end_idx = i + 1
                break
                
    if end_idx != -1:
        old_function = html[start_idx:end_idx]
        new_function = """function waLink(cel,msg){
  if(!cel)return null;
  const d=cel.replace(/\\D/g,'');
  let cc='';
  if(cel.trim().startsWith('+')){
    cc=d;
  }else{
    const DDDs=new Set(['11','12','13','14','15','16','17','18','19',
      '21','22','24','27','28','31','32','33','34','35','37','38',
      '41','42','43','44','45','46','47','48','49','51','53','54','55',
      '61','62','63','64','65','66','67','68','69','71','73','74','75','77','79',
      '81','82','83','84','85','86','87','88','89','91','92','93','94','95','96','97','98','99']);
    if(d.length>=10 && DDDs.has(d.slice(0,2)) && d[2]!=='0'){
      cc='55'+d;
    }else{
      cc='1'+d; // Default to USA / international
    }
  }
  return`https://wa.me/${cc}?text=${encodeURIComponent(msg)}`;
}"""
        html = html[:start_idx] + new_function + html[end_idx:]
        print("Successfully replaced waLink function using string slicing!")
    else:
        print("Error: Could not find matching closing bracket for waLink")
else:
    print("Error: Could not find waLink function start in HTML")

with open("inad_whatsapp.html", "w", encoding="utf-8") as f:
    f.write(html)

print("Saved updated inad_whatsapp.html.")
