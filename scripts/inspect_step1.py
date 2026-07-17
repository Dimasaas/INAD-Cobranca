import json

transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript_full.jsonl"

with open(transcript_path, 'r', encoding='utf-8') as f:
    lines = f.readlines()

print("Total lines in transcript:", len(lines))
# Let's inspect step 1 (index 1)
data = json.loads(lines[1])
print("Step 1 keys:", list(data.keys()))
for k, v in data.items():
    if isinstance(v, (str, list, dict)):
        print(f"Key '{k}': type={type(v)}, length/size={len(v)}")
        if isinstance(v, str):
            print("Start of v:", v[:300])
        elif isinstance(v, list):
            print("First item:", str(v[0])[:300] if v else "empty")
