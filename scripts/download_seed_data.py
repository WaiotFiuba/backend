from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = Path(__file__).with_name("seed_data_manifest.json")
DEFAULT_TIMEOUT_SECONDS = 60
CHUNK_SIZE = 1024 * 1024


def main() -> int:
    args = _parse_args()
    manifest = _load_manifest(args.manifest)
    base_url = _resolve_base_url(args, manifest)
    files = manifest.get("files")
    if not isinstance(files, list) or not files:
        raise SystemExit("El manifest no contiene una lista 'files' valida.")

    downloaded = 0
    skipped = 0
    for item in files:
        result = _ensure_file(
            item=item,
            base_url=base_url,
            dest_root=args.dest_root,
            force=args.force,
            timeout=args.timeout,
        )
        if result == "downloaded":
            downloaded += 1
        elif result == "skipped":
            skipped += 1

    print(f"\nListo. Descargados: {downloaded}. Ya presentes: {skipped}.")
    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Descarga datos semilla pesados desde un GitHub Release."
    )
    parser.add_argument(
        "--manifest",
        type=Path,
        default=DEFAULT_MANIFEST,
        help="Ruta al manifest JSON. Default: scripts/seed_data_manifest.json.",
    )
    parser.add_argument(
        "--dest-root",
        type=Path,
        default=BACKEND_ROOT,
        help="Raiz destino para paths relativos del manifest. Default: backend/.",
    )
    parser.add_argument(
        "--base-url",
        default=None,
        help="URL base del release. Pisa manifest.base_url.",
    )
    parser.add_argument(
        "--tag",
        default=None,
        help=(
            "Tag del release de WaiotFiuba/backend. "
            "Ejemplo: --tag seed-data-v1."
        ),
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Redescarga archivos aunque ya existan.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=DEFAULT_TIMEOUT_SECONDS,
        help=f"Timeout por request en segundos. Default: {DEFAULT_TIMEOUT_SECONDS}.",
    )
    return parser.parse_args()


def _load_manifest(path: Path) -> dict:
    if not path.exists():
        raise SystemExit(f"No existe el manifest: {path}")
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve_base_url(args: argparse.Namespace, manifest: dict) -> str:
    if args.base_url:
        return args.base_url.rstrip("/")
    if args.tag:
        return f"https://github.com/WaiotFiuba/backend/releases/download/{args.tag}"
    env_url = os.environ.get("WAIOT_SEED_DATA_BASE_URL")
    if env_url:
        return env_url.rstrip("/")
    base_url = manifest.get("base_url")
    if not base_url:
        raise SystemExit(
            "No hay base_url en el manifest. Usa --base-url, --tag "
            "o WAIOT_SEED_DATA_BASE_URL."
        )
    return str(base_url).rstrip("/")


def _ensure_file(
    item: dict,
    base_url: str,
    dest_root: Path,
    force: bool,
    timeout: int,
) -> str:
    rel_path = item.get("path")
    expected_sha256 = item.get("sha256")
    if not rel_path or not expected_sha256:
        raise SystemExit(f"Entrada invalida en manifest: {item!r}")

    dest_path = dest_root / rel_path
    if dest_path.exists() and not force:
        actual_sha256 = _sha256_file(dest_path)
        if actual_sha256 == expected_sha256:
            print(f"[ok] {rel_path}")
            return "skipped"
        raise SystemExit(
            f"Hash invalido para archivo existente: {rel_path}\n"
            f"  esperado: {expected_sha256}\n"
            f"  actual:   {actual_sha256}\n"
            "Usa --force para redescargarlo."
        )

    url = item.get("url") or _asset_url(base_url, str(item.get("asset") or rel_path))
    compression = item.get("compression")
    print(f"[download] {rel_path}")
    _download_to_destination(
        url=str(url),
        dest_path=dest_path,
        expected_sha256=str(expected_sha256),
        compression=str(compression) if compression else None,
        timeout=timeout,
    )
    return "downloaded"


def _asset_url(base_url: str, asset: str) -> str:
    return f"{base_url}/{quote(asset)}"


def _download_to_destination(
    url: str,
    dest_path: Path,
    expected_sha256: str,
    compression: str | None,
    timeout: int,
) -> None:
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_dir = Path(tempfile.mkdtemp(prefix="waiot-seed-data-"))
    download_path = tmp_dir / "download.part"
    output_path = tmp_dir / "output.part"
    try:
        _download(url, download_path, timeout)
        if compression == "gzip":
            with gzip.open(download_path, "rb") as src, output_path.open("wb") as dst:
                shutil.copyfileobj(src, dst)
        elif compression in (None, ""):
            output_path = download_path
        else:
            raise SystemExit(f"Compresion no soportada: {compression}")

        actual_sha256 = _sha256_file(output_path)
        if actual_sha256 != expected_sha256:
            raise SystemExit(
                f"Hash invalido para {dest_path.name}\n"
                f"  esperado: {expected_sha256}\n"
                f"  actual:   {actual_sha256}"
            )

        shutil.move(str(output_path), dest_path)
        print(f"  -> {dest_path}")
    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


def _download(url: str, dest_path: Path, timeout: int) -> None:
    request = Request(url, headers={"User-Agent": "waiot-seed-data-downloader"})
    try:
        with urlopen(request, timeout=timeout) as response:
            total = response.headers.get("Content-Length")
            expected_size = int(total) if total and total.isdigit() else None
            read_size = 0
            with dest_path.open("wb") as fh:
                while True:
                    chunk = response.read(CHUNK_SIZE)
                    if not chunk:
                        break
                    fh.write(chunk)
                    read_size += len(chunk)
                    _print_progress(read_size, expected_size)
            print()
    except HTTPError as exc:
        raise SystemExit(f"No se pudo descargar {url}: HTTP {exc.code}") from exc
    except URLError as exc:
        raise SystemExit(f"No se pudo descargar {url}: {exc.reason}") from exc


def _print_progress(read_size: int, expected_size: int | None) -> None:
    if expected_size:
        pct = min(100.0, read_size * 100.0 / expected_size)
        message = f"\r  {pct:5.1f}% ({read_size}/{expected_size} bytes)"
    else:
        message = f"\r  {read_size} bytes"
    print(message, end="", file=sys.stdout, flush=True)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
