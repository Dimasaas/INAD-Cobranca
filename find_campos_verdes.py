import json

transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript_full.jsonl"

with open(transcript_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Total lines:", len(lines))
# print details of line 27
data = json.loads(lines[27])
print("Keys of line 27:", list(data.keys()))
print("Type of line 27:", data.get("type"))
# print snippet of line 27 content
content_str = json.dumps(data)
print("Line 27 contains 'NAYUME'?", "NAYUME" in content_str)
print("Line 27 contains 'CAMPOS VERDES'?", "CAMPOS VERDES" in content_str)

# Let's search for "CAMPOS VERDES" in all lines and print line index and type
for idx, l in enumerate(lines):
    if "CAMPOS VERDES" in l:
        d = json.loads(l)
        print(f"Line {idx}: type={d.get('type')}, keys={list(d.keys())}")
        # print where it is
        for k, v in d.items():
            if isinstance(v, str) and "CAMPOS VERDES" in v:
                print(f"  Key '{k}': starts with '{v[:100]}'")
            elif isinstance(v, list):
                for j, item in enumerate(v):
                    if isinstance(item, str) and "CAMPOS VERDES" in item:
                        print(f"  List Key '{k}[{j}]': starts with '{item[:100]}'")
                    elif isinstance(item, dict):
                        for subk, subv in item.items():
                            if isinstance(subv, str) and "CAMPOS VERDES" in subv:
                                print(f"  Dict List Key '{k}[{j}][{subk}]': starts with '{subv[:100]}'")
