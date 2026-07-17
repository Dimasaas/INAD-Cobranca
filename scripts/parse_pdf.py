import sys
import os

user_site = "/Users/dimas/Library/Python/3.9/lib/python/site-packages"
if user_site not in sys.path:
    sys.path.append(user_site)

from pypdf import PdfReader

pdf_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.user_uploaded/media__1784295071848.pdf"

if not os.path.exists(pdf_path):
    print("PDF file not found at:", pdf_path)
    sys.exit(1)

reader = PdfReader(pdf_path)
print(f"Loaded PDF with {len(reader.pages)} pages.")

# Let's extract and print the text of page 1 to see how it looks
print("--- PAGE 1 TEXT ---")
print(reader.pages[0].extract_text()[:1000])
