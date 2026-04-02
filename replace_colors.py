import os
import re

CSS_DIR = r"c:\Users\ppedo\OneDrive\Documentos\Sistema de nominas\templates"

patterns = [
    # Backgrounds
    (r'background(?:-color)?:\s*(?:white|#ffffff|#fff)\b\s*;', r'background: var(--bg-card);'),
    (r'background(?:-color)?:\s*#(?:ffffff|fff|f9fafb|f3f4f6|eff6ff|ecfdf5|f5f3ff|fffbeb|fef2f2)(?: !important)?\s*([;}"\'])', r'background: var(--bg-card)\1'),
    (r'background(?:-color)?:\s*rgba\(255,\s*255,\s*255,\s*0\.[0-9]+\)\s*([;}"\'])', r'background: var(--bg-card)\1'),
    
    # Texts
    (r'color:\s*#(?:333|222|111|000|1f2937|111827|374151|4b5563|0F172A)\b\s*([;}"\'])', r'color: var(--text-main)\1'),
    (r'color:\s*#(?:666|555|777|999|6b7280|9ca3af|64748B)\b\s*([;}"\'])', r'color: var(--text-muted)\1'),
    
    # Borders
    (r'border:\s*1px solid #(?:e5e7eb|e2e8f0|ccc|ddd)\b\s*([;}"\'])', r'border: 1px solid var(--border)\1'),
    (r'border-bottom:\s*1px solid #(?:e5e7eb|f3f4f6|e2e8f0|ccc|ddd)\b\s*([;}"\'])', r'border-bottom: 1px solid var(--border)\1'),
    (r'border-top:\s*1px solid #(?:e5e7eb|f3f4f6|e2e8f0|ccc|ddd)\b\s*([;}"\'])', r'border-top: 1px solid var(--border)\1'),
]

count_files = 0
count_reps = 0

for root, _, files in os.walk(CSS_DIR):
    for file in files:
        if file.endswith('.html'):
            path = os.path.join(root, file)
            with open(path, 'r', encoding='utf-8') as f:
                content = f.read()
                
            original = content
            for pat, repl in patterns:
                content = re.sub(pat, repl, content, flags=re.IGNORECASE)
                
            if content != original:
                with open(path, 'w', encoding='utf-8') as f:
                    f.write(content)
                count_files += 1
                count_reps += (len(original) - len(content)) # rough delta
                print(f"Modificado: {file}")

print(f"Archivos modificados: {count_files}")
