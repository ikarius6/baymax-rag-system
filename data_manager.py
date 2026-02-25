"""
data_manager.py - Export and import all Baymax RAG data as a single zip file.

Exports / imports:
  - data/          (CSV files from app_confluence.py, app_github.py)
  - chroma_db/     (vector store from index_generator.py)
  - Neo4j graph    (nodes + relationships from graph_builder.py)

Usage:
  python data_manager.py export              # -> backups/baymax_backup_<timestamp>.zip
  python data_manager.py export my_backup    # -> backups/my_backup.zip
  python data_manager.py import backups/baymax_backup_20250225_120000.zip
"""

import gc
import os
import sys
import subprocess
import time
import shutil
import tempfile
import zipfile
from datetime import datetime
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(override=True)

# --- Configuration ---
BASE_DIR = Path(__file__).parent
DATA_DIR = BASE_DIR / "data"
CHROMA_DIR = BASE_DIR / "chroma_db"
CHROMA_STAGING_DIR = BASE_DIR / "chroma_db_import"
BACKUPS_DIR = BASE_DIR / "backups"
NEO4J_DUMP_ARCNAME = "neo4j.dump"

NEO4J_CONTAINER = "baymax-neo4j"
NEO4J_IMAGE = "neo4j:5-community"


# ──────────────────────────────────────────────
# Neo4j helpers  (native dump / load via Docker)
# ──────────────────────────────────────────────
def _docker_run(args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run a command, raising RuntimeError with stderr on failure."""
    result = subprocess.run(args, capture_output=True, text=True, **kwargs)
    if result.returncode != 0:
        raise RuntimeError(result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")
    return result


def _neo4j_export(dest_file: Path):
    """Export Neo4j database to a binary dump file via neo4j-admin.

    Stops the container, dumps via a temporary container that mounts
    the same data volume, then starts the container again.
    """
    volume = _neo4j_get_data_volume()

    # Docker needs forward-slash paths on Windows
    dest_dir = str(dest_file.parent.resolve()).replace("\\", "/")

    print("  Stopping Neo4j container...")
    _docker_run(["docker", "stop", NEO4J_CONTAINER])

    print("  Dumping Neo4j database...")
    _docker_run([
        "docker", "run", "--rm",
        "-v", f"{volume}:/data",
        "-v", f"{dest_dir}:/backups",
        NEO4J_IMAGE,
        "neo4j-admin", "database", "dump", "neo4j",
        "--to-path=/backups/",
        "--overwrite-destination=true",
    ])

    print("  Starting Neo4j container...")
    _docker_run(["docker", "start", NEO4J_CONTAINER])

    size_mb = dest_file.stat().st_size / (1024 * 1024)
    print(f"  Neo4j dump exported ({size_mb:.1f} MB)")


def _neo4j_get_data_volume() -> str:
    """Return the Docker volume name mounted at /data in the Neo4j container."""
    import json as _json
    result = _docker_run(["docker", "inspect", NEO4J_CONTAINER])
    info = _json.loads(result.stdout)
    for mount in info[0].get("Mounts", []):
        if mount.get("Destination") == "/data":
            return mount["Name"]
    raise RuntimeError("Could not determine Neo4j data volume from container")


def _neo4j_import(dump_file: Path):
    """Restore Neo4j from a binary dump file via neo4j-admin.

    Stops the container, loads the dump in a temporary container that
    mounts the same data volume, then starts the container again.
    """
    if not dump_file.exists():
        raise FileNotFoundError(f"Dump file not found: {dump_file}")

    volume = _neo4j_get_data_volume()

    # Ensure the dump is named neo4j.dump (neo4j-admin expects <database>.dump)
    staging_dir = dump_file.parent
    expected = staging_dir / "neo4j.dump"
    if dump_file != expected:
        shutil.copy2(dump_file, expected)

    # Docker needs forward-slash paths on Windows
    mount_path = str(staging_dir.resolve()).replace("\\", "/")

    print("  Stopping Neo4j container...")
    _docker_run(["docker", "stop", NEO4J_CONTAINER])

    print("  Loading dump into Neo4j...")
    _docker_run([
        "docker", "run", "--rm",
        "-v", f"{volume}:/data",
        "-v", f"{mount_path}:/backups",
        NEO4J_IMAGE,
        "neo4j-admin", "database", "load", "neo4j",
        "--from-path=/backups/",
        "--overwrite-destination=true",
    ])

    print("  Starting Neo4j container...")
    _docker_run(["docker", "start", NEO4J_CONTAINER])
    print("  Neo4j restored.")


# ──────────────────────────────────────────────
# Zip helpers
# ──────────────────────────────────────────────
def _add_directory_to_zip(zipf: zipfile.ZipFile, dir_path: Path, arc_prefix: str):
    """Recursively add a directory to a zip file."""
    if not dir_path.exists():
        print(f"  Skipping {dir_path} (not found)")
        return
    count = 0
    for file in dir_path.rglob("*"):
        if file.is_file():
            arcname = f"{arc_prefix}/{file.relative_to(dir_path)}"
            zipf.write(file, arcname)
            count += 1
    print(f"  Added {count} files from {arc_prefix}/")


def _extract_directory_from_zip(zipf: zipfile.ZipFile, arc_prefix: str, dest: Path):
    """Extract files under arc_prefix/ from zip into dest directory."""
    members = [m for m in zipf.namelist() if m.startswith(arc_prefix + "/")]
    if not members:
        print(f"  No {arc_prefix}/ found in zip — skipping")
        return

    # Clear destination (retry on Windows file-lock errors)
    if dest.exists():
        for attempt in range(5):
            try:
                shutil.rmtree(dest)
                break
            except PermissionError:
                gc.collect()
                time.sleep(1)
                if attempt == 4:
                    raise
    dest.mkdir(parents=True, exist_ok=True)

    for member in members:
        rel = member[len(arc_prefix) + 1:]  # strip prefix + /
        if not rel:
            continue
        target = dest / rel
        if member.endswith("/"):
            target.mkdir(parents=True, exist_ok=True)
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            with zipf.open(member) as src, open(target, "wb") as dst:
                shutil.copyfileobj(src, dst)

    print(f"  Restored {len(members)} entries to {dest.name}/")


# ──────────────────────────────────────────────
# Public API
# ──────────────────────────────────────────────
def export_data(zip_name: str | None = None) -> Path:
    """Export data/, chroma_db/, and Neo4j into a single zip file.

    Returns the path of the created zip.
    """
    BACKUPS_DIR.mkdir(exist_ok=True)

    if not zip_name:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        zip_name = f"baymax_backup_{ts}"
    if not zip_name.endswith(".zip"):
        zip_name += ".zip"

    zip_path = BACKUPS_DIR / zip_name
    print(f"Exporting to {zip_path} ...")

    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
        # 1. data/ folder
        _add_directory_to_zip(zipf, DATA_DIR, "data")

        # 2. chroma_db/ folder
        _add_directory_to_zip(zipf, CHROMA_DIR, "chroma_db")

        # 3. Neo4j binary dump via neo4j-admin
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                dump_file = Path(tmpdir) / "neo4j.dump"
                _neo4j_export(dump_file)
                zipf.write(dump_file, NEO4J_DUMP_ARCNAME)
                print(f"  Added Neo4j dump to zip")
        except Exception as e:
            print(f"  Neo4j export skipped ({e})")

    size_mb = zip_path.stat().st_size / (1024 * 1024)
    print(f"Export complete: {zip_path} ({size_mb:.1f} MB)")
    return zip_path


def _import_neo4j_from_zip(zipf: zipfile.ZipFile):
    """Extract and load the Neo4j dump from an open zip file."""
    if NEO4J_DUMP_ARCNAME not in zipf.namelist():
        print("  No Neo4j dump in zip — skipping")
        return
    try:
        with tempfile.TemporaryDirectory() as tmpdir:
            dump_file = Path(tmpdir) / "neo4j.dump"
            with zipf.open(NEO4J_DUMP_ARCNAME) as src, open(dump_file, "wb") as dst:
                shutil.copyfileobj(src, dst)
            _neo4j_import(dump_file)
    except Exception as e:
        print(f"  Neo4j import failed ({e})")
        print("  Make sure Docker is running: docker compose up -d")


def import_data(zip_path: str | Path):
    """Import data/, chroma_db/, and Neo4j from a backup zip file.

    Replaces data/ and chroma_db/ in-place.  Works from CLI when Streamlit
    is NOT running.  If Streamlit holds a lock on chroma_db, use
    import_data_staged() instead.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"Backup not found: {zip_path}")

    print(f"Importing from {zip_path} ...")

    with zipfile.ZipFile(zip_path, "r") as zipf:
        _extract_directory_from_zip(zipf, "data", DATA_DIR)
        _extract_directory_from_zip(zipf, "chroma_db", CHROMA_DIR)
        _import_neo4j_from_zip(zipf)

    print("Import complete!")


def import_data_staged(zip_path: str | Path):
    """Import data + Neo4j immediately; stage chroma_db for swap on next restart.

    Use this when ChromaDB is locked (e.g. called from Streamlit).  The caller
    must ensure finalize_chroma_swap() runs *before* ChromaDB is opened again.
    """
    zip_path = Path(zip_path)
    if not zip_path.exists():
        raise FileNotFoundError(f"Backup not found: {zip_path}")

    print(f"Importing (staged) from {zip_path} ...")

    with zipfile.ZipFile(zip_path, "r") as zipf:
        _extract_directory_from_zip(zipf, "data", DATA_DIR)
        _extract_directory_from_zip(zipf, "chroma_db", CHROMA_STAGING_DIR)
        _import_neo4j_from_zip(zipf)

    print("Staged import complete — chroma_db will swap on next reload.")


def finalize_chroma_swap():
    """Swap chroma_db_import/ → chroma_db/.  Call BEFORE ChromaDB is opened.

    Returns True if a swap happened.
    """
    if not CHROMA_STAGING_DIR.exists():
        return False

    print("Finalizing chroma_db swap...")
    if CHROMA_DIR.exists():
        shutil.rmtree(CHROMA_DIR)
    CHROMA_STAGING_DIR.rename(CHROMA_DIR)
    print("  chroma_db replaced from staged import.")
    return True


def list_backups() -> list[Path]:
    """Return available backup zip files sorted by modification time (newest first)."""
    if not BACKUPS_DIR.exists():
        return []
    zips = sorted(BACKUPS_DIR.glob("*.zip"), key=lambda p: p.stat().st_mtime, reverse=True)
    return zips


# ──────────────────────────────────────────────
# CLI
# ──────────────────────────────────────────────
def main():
    usage = (
        "Usage:\n"
        "  python data_manager.py export [name]       Export all data to backups/<name>.zip\n"
        "  python data_manager.py import <path.zip>   Import all data from a zip backup\n"
        "  python data_manager.py list                List available backups"
    )

    if len(sys.argv) < 2:
        print(usage)
        sys.exit(1)

    action = sys.argv[1].lower()

    if action == "export":
        name = sys.argv[2] if len(sys.argv) > 2 else None
        export_data(name)

    elif action == "import":
        if len(sys.argv) < 3:
            print("Error: provide the path to the zip file.")
            print(usage)
            sys.exit(1)
        import_data(sys.argv[2])

    elif action == "list":
        backups = list_backups()
        if not backups:
            print("No backups found in backups/")
        else:
            print(f"Found {len(backups)} backup(s):")
            for b in backups:
                size_mb = b.stat().st_size / (1024 * 1024)
                mtime = datetime.fromtimestamp(b.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                print(f"  {b.name:50s}  {size_mb:>7.1f} MB  {mtime}")

    else:
        print(f"Unknown action: {action}")
        print(usage)
        sys.exit(1)


if __name__ == "__main__":
    main()
