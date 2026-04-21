import os
import json
from pathlib import Path

# Configuración de rutas
SOURCE_FOLDERS = [
    r"C:\Users\user\.gemini\antigravity\brain\eba90de8-e934-47cb-93b9-fd15fc0abdd3",
    r"C:\Users\user\.gemini\antigravity\brain\37c03c60-3054-44d7-89cf-7dcfad8a94bf"
]
OUTPUT_FILE = r"C:\Users\user\OneDrive\Escritorio\gemini cli\trader\AUDITORIA\HISTORIAL_COMPLETO.txt"

def process_history():
    with open(OUTPUT_FILE, "w", encoding="utf-8") as out:
        out.write("================================================================================\n")
        out.write("RECONSTRUCCIÓN INTEGRAL DEL HISTORIAL DE DESARROLLO - ABRIL 2026\n")
        out.write("================================================================================\n\n")

        for folder_path in SOURCE_FOLDERS:
            folder = Path(folder_path)
            if not folder.exists():
                out.write(f"\n[ERROR]: Carpeta no encontrada: {folder_path}\n")
                continue

            out.write(f"\n\n>>> INICIO DE SESIÓN: {folder.name} <<<\n")
            out.write("-" * 80 + "\n")

            # Procesar archivos de la sesión de forma recursiva
            for file_path in folder.rglob("*"):
                if file_path.is_dir():
                    continue

                # Filtrar por extensión
                if file_path.suffix.lower() not in [".json", ".md", ".txt"]:
                    continue

                out.write(f"\n\n--- ARCHIVO: {file_path.relative_to(folder)} ---\n")
                
                try:
                    if file_path.suffix.lower() == ".json":
                        # Solo procesar como mensaje si está en una carpeta 'messages'
                        if "messages" in str(file_path):
                            with open(file_path, "r", encoding="utf-8") as f:
                                data = json.load(f)
                                sender = data.get("sender", "UNKNOWN")
                                content = data.get("content", "")
                                out.write(f"[{sender.upper()}]: {content}\n")
                        else:
                            # Si es otro JSON (como metadatos), copiarlo íntegro
                            with open(file_path, "r", encoding="utf-8") as f:
                                out.write(f.read() + "\n")
                    
                    elif file_path.suffix.lower() in [".md", ".txt"]:
                        # Copia íntegra para Markdown y Texto (logs)
                        with open(file_path, "r", encoding="utf-8", errors="ignore") as f:
                            out.write(f.read() + "\n")

                except Exception as e:
                    out.write(f"\n[ERROR PROCESANDO ARCHIVO]: {str(e)}\n")

    print(f"✅ Historial reconstruido con éxito en: {OUTPUT_FILE}")

if __name__ == "__main__":
    process_history()
