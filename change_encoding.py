
from pathlib import Path
import argparse
import codecs

# Extensions des fichiers à convertir
EXTENSIONS = {
    ".py", ".json", ".txt", ".md", ".csv",
    ".xml", ".html", ".css", ".js", ".yaml", ".yml"
}


def convert_encoding(path: Path, target_encoding: str = "utf-8") -> None:
    raw = path.read_bytes()

    # Détection des encodages courants
    if raw.startswith(codecs.BOM_UTF8):
        text = raw.decode("utf-8-sig")
    else:
        for encoding in ("utf-8", "cp1252", "iso-8859-1"):
            try:
                text = raw.decode(encoding)
                break
            except UnicodeDecodeError:
                continue

    converted = text.encode(target_encoding)

    if raw != converted:
        path.write_bytes(converted)
        print(f"[CONVERTI] {path}")
    else:
        print(f"[OK] {path}")


def convert_folder(folder: Path, recursive: bool = True) -> None:
    files = folder.rglob("*") if recursive else folder.glob("*")

    for path in files:
        if not path.is_file() or path.suffix.lower() not in EXTENSIONS:
            continue

        try:
            convert_encoding(path)
        except (OSError, UnicodeError) as exc:
            print(f"[ERREUR] {path}: {exc}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("folder", type=Path)
    parser.add_argument("--no-recursive", action="store_true")
    args = parser.parse_args()

    if not args.folder.is_dir():
        parser.error("Le dossier spécifié n'existe pas.")

    convert_folder(args.folder, recursive=not args.no_recursive)
