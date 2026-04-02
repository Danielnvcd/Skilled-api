import os
import re

css_dir = r"c:\Users\ppedo\OneDrive\Documentos\Sistema de nominas\static\css"
pattern = re.compile(r':root\s*\{[^}]*\}', re.DOTALL)

for file in os.listdir(css_dir):
    if file.endswith('.css') and file != 'base.css':
        path = os.path.join(css_dir, file)
        with open(path, 'r', encoding='utf-8') as f:
            content = f.read()
        
        if ':root' in content:
            new_content = re.sub(pattern, '', content)
            with open(path, 'w', encoding='utf-8') as f:
                f.write(new_content)
            print(f"Removed :root from {file}")
