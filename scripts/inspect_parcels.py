import re

with open("pdf_text.txt", "r", encoding="utf-8") as f:
    text = f.read()

# Let's find all instances of \d+/\d+ in the text and print their surrounding characters
matches = re.findall(r'.{0,15}\d+/\d+.{0,15}', text)
print(f"Total \\d+/\\d+ matches: {len(matches)}")
print("First 20 matches:")
for m in matches[:20]:
    print(repr(m))
