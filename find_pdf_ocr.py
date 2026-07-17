import json

transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript_full.jsonl"

with open(transcript_path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        if "Start of PDF" in line:
            print(f"Line {i} contains 'Start of PDF'!")
            # parse the JSON
            data = json.loads(line)
            print("Keys:", list(data.keys()))
            print("Type:", data.get("type"))
            # find where it is
            for k, v in data.items():
                if isinstance(v, str) and "Start of PDF" in v:
                    print(f"Found in key: {k}, length: {len(v)}")
                    # Save a snippet
                    idx = v.find("Start of PDF")
                    print("Snippet around it:", v[idx-100:idx+300])
                elif isinstance(v, dict):
                    # check recursively
                    pass
                elif isinstance(v, list):
                    pass
