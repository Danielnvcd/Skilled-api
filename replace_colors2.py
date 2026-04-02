import os
import re

CSS_DIRS = [
    r"c:\Users\ppedo\OneDrive\Documentos\Sistema de nominas\static\css",
    r"c:\Users\ppedo\OneDrive\Documentos\Sistema de nominas\templates"
]

patterns = [
    # Cazar CUALQUIER variante de rgba(255,255,255...) con o sin important
    (r'background(?:-color)?:\s*rgba\(25[0-5],\s*25[0-5],\s*25[0-5][^)]*\)[^;}"\']*([;}"\'])', r'background: var(--bg-card)\1'),
    # Cazar todos los blancos/grises crudos que empiecen por #F o #e
    (r'background(?:-color)?:\s*#(?:[fFeE][A-Fa-f0-9]{5}|[fFeE][A-Fa-f0-9]{2})\b[^;}"\']*([;}"\'])', r'background: var(--bg-card)\1'),
    (r'background(?:-color)?:\s*white\b[^;}"\']*([;}"\'])', r'background: var(--bg-card)\1'),
    # Gradiente de blancos
    (r'background(?:-color)?:\s*linear-gradient\([^)]*(?:#fff|white|#f[A-Fa-f0-9]{2,5})[^)]*\)[^;}"\']*([;}"\'])', r'background: var(--bg-card)\1'),
]

for d in CSS_DIRS:
    for root, _, files in os.walk(d):
        for file in files:
            if file.endswith('.css') or file.endswith('.html'):
                path = os.path.join(root, file)
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                    
                original = content
                for pat, repl in patterns:
                    content = re.sub(pat, repl, content, flags=re.IGNORECASE)
                
                # Cache busting for any .css inclusion
                if file.endswith('.html'):
                    content = re.sub(r'(\.css)(?:\?v=[0-9]+)?(["\'])', r'\1?v=20\2', content)

                if content != original:
                    with open(path, 'w', encoding='utf-8') as f:
                        f.write(content)
                    print(f"Modificado: {file}")

print("Listo!")
