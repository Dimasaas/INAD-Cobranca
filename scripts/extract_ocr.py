import json
import re
import os

transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript_full.jsonl"
if not os.path.exists(transcript_path):
    transcript_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.system_generated/logs/transcript.jsonl"

print("Reading transcript from:", transcript_path)

ocr_text = ""
with open(transcript_path, 'r', encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        if data.get("type") == "USER_INPUT":
            content = data.get("content", "")
            ocr_text += content

print("Total length of user input text:", len(ocr_text))

# Let's write the OCR text to a file so we can analyze it
with open("ocr_extracted.txt", "w", encoding="utf-8") as out:
    out.write(ocr_text)

print("Saved OCR content to ocr_extracted.txt")
