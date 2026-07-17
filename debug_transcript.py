import json
import os

transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript_full.jsonl"

with open(transcript_path, 'r', encoding='utf-8') as f:
    for i, line in enumerate(f):
        data = json.loads(line)
        print(f"Step {i}: type={data.get('type')}, keys={list(data.keys())}")
        if data.get("type") == "USER_INPUT":
            # print first 500 chars of content
            content = data.get("content", "")
            print("Content start:", content[:200])
            print("Content length:", len(content))
            # search for Start of PDF
            if "Start of PDF" in content:
                print("Found Start of PDF inside content!")
            else:
                print("Start of PDF NOT found inside content!")
                # Let's check other keys
                for k, v in data.items():
                    if isinstance(v, str) and "Start of PDF" in v:
                        print(f"Found in key: {k}")
                    elif isinstance(v, list):
                        for j, item in enumerate(v):
                            if isinstance(item, str) and "Start of PDF" in item:
                                print(f"Found in list key: {k}[{j}]")
                            elif isinstance(item, dict):
                                for subk, subv in item.items():
                                    if isinstance(subv, str) and "Start of PDF" in subv:
                                        print(f"Found in dict list key: {k}[{j}][{subk}]")
