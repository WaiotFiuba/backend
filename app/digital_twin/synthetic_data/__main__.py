from __future__ import annotations

import argparse
import json
from pathlib import Path

from app.digital_twin.synthetic_data.exporters.files import export_simulation
from app.digital_twin.synthetic_data.simulation.engine import SyntheticDataSimulator
from app.digital_twin.synthetic_data.simulation.scenario import load_scenario
from app.digital_twin.synthetic_data.validation.checks import validate_result


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera datasets sinteticos para el gemelo digital de Waiot.")
    parser.add_argument(
        "--scenario",
        type=Path,
        default=Path("app/digital_twin/synthetic_data/config/semana_normal.yaml"),
        help="Ruta a un escenario YAML o JSON.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("datos/sinteticos"),
        help="Directorio destino para los datasets generados.",
    )
    parser.add_argument(
        "--api-payloads",
        action="store_true",
        help="Genera api_payloads.jsonl ademas de los archivos base.",
    )
    parser.add_argument(
        "--include-topology",
        action="store_true",
        help="Exporta sites.csv, containers.csv y devices.csv aunque la topologia venga del backend.",
    )
    parser.add_argument(
        "--format",
        choices=("csv", "parquet"),
        default="csv",
        help="Formato para measurements, collections y alarms. Por defecto: csv.",
    )
    parser.add_argument(
        "--from-backend",
        action="store_true",
        help="Usa los contenedores expuestos por el backend en vez de crear topologia sintetica.",
    )
    parser.add_argument(
        "--backend-url",
        default="http://localhost:8000",
        help="URL base del backend cuando se usa --from-backend.",
    )
    parser.add_argument(
        "--containers-path",
        default="/map/containers/",
        help="Path del endpoint de contenedores cuando se usa --from-backend.",
    )
    parser.add_argument(
        "--backend-token",
        default=None,
        help="Bearer token opcional para consultar el backend.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Alias legacy de --container-limit.",
    )
    parser.add_argument(
        "--container-limit",
        type=int,
        default=None,
        help="Cantidad máxima de contenedores reales a simular cuando se usa --from-backend.",
    )
    args = parser.parse_args()

    config = load_scenario(args.scenario)
    container_limit = args.container_limit if args.container_limit is not None else args.limit
    if container_limit is None:
        container_limit = config.container_limit
    topology = _load_backend_topology(
        backend_url=args.backend_url,
        containers_path=args.containers_path,
        limit=container_limit,
        token=args.backend_token,
    ) if args.from_backend else None
    result = SyntheticDataSimulator(config, topology=topology).run()
    report = validate_result(result)
    if not report.ok:
        raise SystemExit("Validacion fallida: " + "; ".join(report.errors))

    paths = export_simulation(
        result,
        args.output,
        api_payloads=args.api_payloads or config.api_payloads,
        export_format=args.format,
        include_topology=args.include_topology or not args.from_backend,
    )
    print(json.dumps({"metrics": report.metrics, "outputs": {key: str(value) for key, value in paths.items()}}, indent=2))


def _load_backend_topology(
    backend_url: str,
    containers_path: str,
    limit: int | None,
    token: str | None,
):
    from app.digital_twin.synthetic_data.loaders.backend_http import (
        BackendConnectionError,
        load_topology_from_backend_api,
    )

    try:
        topology = load_topology_from_backend_api(
            backend_url=backend_url,
            containers_path=containers_path,
            limit=limit,
            token=token,
        )
    except BackendConnectionError as exc:
        raise SystemExit(f"Error: {exc}") from None

    if not topology.containers:
        raise SystemExit("No se encontraron contenedores en el backend para simular.")

    return topology


if __name__ == "__main__":
    main()
