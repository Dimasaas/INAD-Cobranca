import sys
import os

user_site = "/Users/dimas/Library/Python/3.9/lib/python/site-packages"
if user_site not in sys.path:
    sys.path.append(user_site)

from pypdf import PdfReader

pdf_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.user_uploaded/media__1784295071848.pdf"
reader = PdfReader(pdf_path)

full_text = ""
for i, page in enumerate(reader.pages):
    full_text += f"=== PAGE {i+1} ===\n"
    # Using layout mode to preserve spatial positioning
    full_text += page.extract_text(extraction_mode="layout") + "\n"

with open("pdf_text.txt", "w", encoding="utf-8") as f:
    f.write(full_text)

print("Saved layout PDF text to pdf_text.txt")
print("Total characters:", len(full_text))
