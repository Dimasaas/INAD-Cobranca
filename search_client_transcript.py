import json

transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript_full.jsonl"

with open(transcript_path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if "CAMPOS VERDES" in line:
            print(f"Line {i} contains 'CAMPOS VERDES'")
            try:
                data = json.loads(line)
                print("Keys:", list(data.keys()))
                print("Type:", data.get("type"))
            except Exception as e:
                print("Error parsing JSON:", e)
        if "NAYUME" in line:
            print(f"Line {i} contains 'NAYUME'")
