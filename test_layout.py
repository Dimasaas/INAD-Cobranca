import sys

user_site = "/Users/dimas/Library/Python/3.9/lib/python/site-packages"
if user_site not in sys.path:
    sys.path.append(user_site)

from pypdf import PdfReader

pdf_path = "/Users/dimas/.gemini/antigravity/brain/fafbd5c4-ced7-433d-91f3-5fe73f683fcc/.user_uploaded/media__1784295071848.pdf"
reader = PdfReader(pdf_path)

print(reader.pages[0].extract_text(extraction_mode="layout")[:3000])
