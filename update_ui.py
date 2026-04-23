import re
import os

path = r"c:\Users\user\OneDrive\Escritorio\gemini cli\trader\static\index.html"
with open(path, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Add Lucide script
lucide_script = '<script src="https://unpkg.com/lucide@latest/dist/umd/lucide.js"></script>'
if "lucide.js" not in content:
    content = content.replace(
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>',
        '<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>\n  ' + lucide_script
    )

# 2. Update CSS Base & Glassmorphism
css_updates = {
    "--bg-primary:    #060a12;": "--bg-primary:    #04070d;\n      --glass-bg:      rgba(10, 16, 28, 0.7);\n      --glass-border:  rgba(255, 255, 255, 0.05);",
    "background: var(--bg-card);": "background: var(--glass-bg); backdrop-filter: blur(12px); -webkit-backdrop-filter: blur(12px); box-shadow: 0 4px 30px rgba(0, 0, 0, 0.5);",
    "background: var(--bg-secondary);": "background: var(--glass-bg); backdrop-filter: blur(16px); -webkit-backdrop-filter: blur(16px);",
    "background: var(--bg-card-alt); border: 1px solid var(--border); border-radius: 10px;": "background: rgba(15, 25, 41, 0.4); backdrop-filter: blur(8px); border: 1px solid var(--glass-border); border-radius: 10px;",
    "font-size: 1.6rem;": "font-size: 2.2rem; letter-spacing: -1px;",
    ".stat-label { font-size: 0.72rem;": ".stat-label { font-size: 0.68rem; opacity: 0.6;",
    "body {": "body {\n      font-family: 'Space Grotesk', sans-serif;\n      background: var(--bg-primary);\n      color: var(--text-primary);\n      min-height: 100vh;\n      overflow-x: hidden;\n    }\n\n    body::before {\n      content: '';\n      position: fixed;\n      top: 0; left: 0; right: 0; bottom: 0;\n      background: \n        radial-gradient(ellipse at 15% 50%, rgba(0,212,255,0.04), transparent 50%),\n        radial-gradient(ellipse at 85% 20%, rgba(123,94,167,0.05), transparent 50%),\n        radial-gradient(ellipse at 50% 80%, rgba(0,230,118,0.02), transparent 40%);\n      pointer-events: none;\n      z-index: -1;\n      transform: translateZ(0);\n    }",
}

for old, new in css_updates.items():
    if old in content and "body::before" not in content if old == "body {" else True:
        # Solo reemplazar la primera instancia exacta de body { para no romper otras declaraciones
        if old == "body {":
             content = content.replace("body {\n      font-family: 'Space Grotesk', sans-serif;\n      background: var(--bg-primary);\n      color: var(--text-primary);\n      min-height: 100vh;\n      overflow-x: hidden;\n    }", new)
        else:
             content = content.replace(old, new)

# 3. Add Zebra striping to data-table
zebra_css = """    .data-table tr:hover td { background: var(--bg-card-alt); }
    .data-table tbody tr:nth-child(even) td { background: rgba(15,25,41,0.3); }"""
if "nth-child(even)" not in content:
    content = content.replace(".data-table tr:hover td { background: var(--bg-card-alt); }", zebra_css)

# 4. Replace Emojis with Lucide Icons
emoji_replacements = {
    '🏠': '<i data-lucide="home" style="width:16px;height:16px;"></i>',
    '📈': '<i data-lucide="trending-up" style="width:16px;height:16px;"></i>',
    '₿': '<i data-lucide="bitcoin" style="width:16px;height:16px;"></i>',
    '⚡': '<i data-lucide="zap" style="width:16px;height:16px;"></i>',
    '🌐': '<i data-lucide="globe" style="width:20px;height:20px;display:inline-block;vertical-align:middle;"></i>',
    '🚀': '<i data-lucide="rocket" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '💵': '<i data-lucide="banknote" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '📊': '<i data-lucide="bar-chart-2" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '📉': '<i data-lucide="trending-down" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '📁': '<i data-lucide="folder-open" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '🔄': '<i data-lucide="refresh-cw" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '🎯': '<i data-lucide="target" style="width:20px;height:20px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '🏆': '<i data-lucide="trophy" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '⏰': '<i data-lucide="clock" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '🛡️': '<i data-lucide="shield-check" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '⚖️': '<i data-lucide="scale" style="width:14px;height:14px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '🔍': '<i data-lucide="search" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
    '🚨': '<i data-lucide="alert-triangle" style="width:24px;height:24px;color:var(--red);"></i>',
    '📋': '<i data-lucide="clipboard-list" style="width:18px;height:18px;display:inline-block;vertical-align:middle;margin-right:4px;"></i>',
}

# The big emoji cards have specific styling
content = content.replace('<div style="font-size: 3.5rem; margin-bottom: 16px;">📈</div>', '<div style="margin-bottom: 16px; color: var(--etf-accent);"><i data-lucide="trending-up" style="width:48px;height:48px;"></i></div>')
content = content.replace('<div style="font-size: 3.5rem; margin-bottom: 16px;">₿</div>', '<div style="margin-bottom: 16px; color: var(--crypto-accent);"><i data-lucide="bitcoin" style="width:48px;height:48px;"></i></div>')
content = content.replace('<div style="font-size: 3.5rem; margin-bottom: 16px;">⚡</div>', '<div style="margin-bottom: 16px; color: var(--eq-accent);"><i data-lucide="zap" style="width:48px;height:48px;"></i></div>')
content = content.replace('<span id="regime-icon" style="font-size: 1.8rem;">🔄</span>', '<span id="regime-icon" style="color:var(--text-primary);"><i data-lucide="refresh-cw" style="width:32px;height:32px;"></i></span>')

for emoji, icon in emoji_replacements.items():
    content = content.replace(emoji, icon)

# 5. Fix JS dynamic emojis
js_emoji_fixes = {
    "const icon = { BULL: '🐂', BEAR: '🐻', CHOP: '🔄', UNKNOWN: '❓' }[regime.regime] || '❓';": "const icon = { BULL: 'trending-up', BEAR: 'trending-down', CHOP: 'refresh-cw', UNKNOWN: 'help-circle' }[regime.regime] || 'help-circle';\n      setEl('regime-icon', '');\n      document.getElementById('regime-icon').innerHTML = `<i data-lucide=\"${icon}\" style=\"width:32px;height:32px;stroke-width:2.5px\"></i>`;\n      setTimeout(() => lucide.createIcons(), 10);",
    "b.textContent = 'MERCADO ABIERTO 🟢';": "b.innerHTML = 'MERCADO ABIERTO <i data-lucide=\"check-circle-2\" style=\"width:14px;height:14px;display:inline-block;vertical-align:middle\"></i>';",
    "b.textContent = 'MERCADO CERRADO 🔴';": "b.innerHTML = 'MERCADO CERRADO <i data-lucide=\"x-circle\" style=\"width:14px;height:14px;display:inline-block;vertical-align:middle\"></i>';",
}

for old, new in js_emoji_fixes.items():
    if old in content:
        content = content.replace(old, new)
        
content = content.replace("setEl('regime-icon', icon);", "// setEl('regime-icon', icon); handled above")

# 6. Add lucide.createIcons() call to the end of JS
if "lucide.createIcons();" not in content:
    content = content.replace("loadStrategyRanking();", "loadStrategyRanking();\n\nsetTimeout(() => lucide.createIcons(), 100);")
    
# Call createIcons inside autoRefresh components as well
if "function autoRefresh() {" in content:
    content = content.replace("function autoRefresh() {", "function autoRefresh() {\n  setTimeout(() => lucide.createIcons(), 250);")

# Save changes
with open(path, "w", encoding="utf-8") as f:
    f.write(content)

print("UI successfully updated.")
