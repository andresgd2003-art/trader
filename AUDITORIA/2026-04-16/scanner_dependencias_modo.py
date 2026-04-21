import os
import re

# Configuración
PROJECT_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))

# Patrones de búsqueda relacionados al sistema de Modos (A/B/C)
PATTERNS = [
    r"daily_mode",
    r"get_active_mode",
    r"get_mode_label",
    r"FORCE_MODE",
    r"\_ACTIVE_MODE",
    r"m[A-Z]_",
    r"strat_[a-zA-Z0-9]+_m[A-C]"
]

def scan_file(filepath):
    """Escanea un archivo línea por línea en busca de los patrones definidos."""
    results = []
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            for line_number, line in enumerate(f, 1):
                for pattern in PATTERNS:
                    if re.search(pattern, line):
                        results.append((line_number, line.strip(), pattern))
                        break # Evitar agregar la misma línea varias veces si hace match con múltiples patrones
    except UnicodeDecodeError:
        pass # Ignorar archivos binarios
    return results

def main():
    print(f"\\n🔍 Iniciando Escaneo de Auditoría: Dependencias de 'Daily Mode'\\n")
    print(f"Directorio Raíz: {PROJECT_ROOT}\\n")
    print("-" * 60)
    
    total_matches = 0
    files_affected = 0
    
    for dirpath, _, filenames in os.walk(PROJECT_ROOT):
        # Ignorar directorios irrelevantes
        if any(exclude in dirpath for exclude in ['.git', '__pycache__', 'venv', 'node_modules', 'AUDITORIA']):
            continue
            
        for filename in filenames:
            # Solo analizar archivos de código (puedes ampliar esta lista)
            if filename.endswith(('.py', '.js', '.html', '.sh')):
                filepath = os.path.join(dirpath, filename)
                matches = scan_file(filepath)
                
                if matches:
                    files_affected += 1
                    rel_path = os.path.relpath(filepath, PROJECT_ROOT)
                    print(f"\\n📁 Archivo: {rel_path} ({len(matches)} coincidencias)")
                    
                    for line_num, content, pattern in matches:
                        total_matches += 1
                        # Truncar líneas muy largas para la consola
                        display_content = content if len(content) < 80 else content[:77] + "..."
                        print(f"  └─ Línea {line_num}: [Regex Match: {pattern}] -> {display_content}")

    print("\\n" + "=" * 60)
    print(f"✅ ESTADÍSTICAS FINALES DEL ESCANEO")
    print(f"Archivos únicos con dependencias lógicas: {files_affected}")
    print(f"Total de líneas de código a refactorizar: ~{total_matches}")
    print("=" * 60)

if __name__ == "__main__":
    main()
