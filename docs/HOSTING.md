# Hosting y despliegue continuo

Este proyecto usa `dev` como rama base de desarrollo y `main` como unica rama
hosteada. Las features salen desde `dev`, vuelven a `dev`, y cuando hay una
version presentable se mergea `dev` a `main`.

La arquitectura actual es:

- **Frontend:** Render.
- **Backend API, base de datos y simulador:** una VM de Oracle Cloud, con Docker
  Compose. Caddy expone la API por HTTPS.

El frontend usa la API de la VM.

## Flujo de ramas

```text
feature/*
  -> dev
     -> main
        -> release automatico desde GitHub Actions
```

- `main`: estable, presentable y deployable. Cada push despliega.
- `dev`: integracion de features. Cada push corre tests y pre-commit.
- `feature/*`: trabajo puntual desde `dev`.

## 1. Release, migraciones y seed

El workflow `Release production backend` corre con cada push a `main`. Tambien
se puede lanzar a mano:

```text
Actions -> Release production backend -> Run workflow
```

Ese workflow:

1. valida que se ejecute desde `main`;
2. instala dependencias;
3. corre tests;
4. si pasan, despliega en la VM de Oracle (ver [seccion 4](#4-oracle-cloud-api-base-y-simulador)).

La API corre las migraciones y el seed al arrancar, y el seed no duplica
contenedores que ya existen. Para ejecutar migraciones manualmente desde una
maquina local:

```bash
uv run alembic upgrade head
```

## 2. Render: frontend

Configuracion sugerida:

- Service type: **Static Site**.
- Branch: `main`.
- Root directory: vacio si el repo conectado es `frontend`; usar `frontend` solo si
  Render esta conectado al monorepo raiz.
- Build command:

```bash
npm install && npm run build
```

- Publish directory:

```text
dist
```

Variables de entorno:

```env
VITE_BACKEND_URL=https://<api-domain>
VITE_AUTH_DEV_BYPASS=false
VITE_MIN_TICK_SECONDS=16
VITE_ENABLE_SIMULATOR=true
```

`VITE_BACKEND_URL` apunta a la API de la VM (`API_DOMAIN`, ver [seccion 4](#4-oracle-cloud-api-base-y-simulador)). Cuando
Render entregue la URL final del frontend, agregarla en la API:

```env
CORS_ALLOWED_ORIGINS=https://your-frontend.onrender.com
```

## 3. Simulador

El simulador corre en la VM como el servicio `simulator`, junto con la API (ver
[seccion 4](#4-oracle-cloud-api-base-y-simulador)). Toma las simulaciones que se crean desde el frontend y envia las
mediciones a la API por la red interna de Docker (`BACKEND_URL=http://api:8000`).

La configuracion del simulador esta en [simulator/README.md](../simulator/README.md).

Tambien se puede correr localmente apuntando a una API hosteada:

```env
BACKEND_URL=https://<api-domain>
```

```bash
uv run python -m simulator.worker
```

## 4. Oracle Cloud: API, base y simulador

La VM corre todo con Docker Compose: `postgis`, `api`, `simulator` y `caddy`.
Caddy expone la API por HTTPS con un certificado de Let's Encrypt.

Requisitos de la VM:

- Docker Engine y Docker Compose.
- Puertos 80 y 443 abiertos en la Security List de la red y en el firewall de la
  VM. La API (8000) y PostGIS (5432) no se publican.

Archivos de produccion:

- `docker-compose.prod.yml`: se aplica encima de `docker-compose.yml`. Saca los
  volumenes de codigo y el `--reload`, no publica los puertos de la API ni de
  PostGIS y suma el servicio `caddy`.
- `deploy/Caddyfile`: HTTPS para `API_DOMAIN`, reenviando a `api:8000`.
- `.env`: se arma a partir de `.env.oracle.example`.

Variables propias de la VM:

```env
COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml
COMPOSE_PROFILES=map
API_DOMAIN=<ip-con-guiones>.sslip.io
SECRET_KEY=replace-with-a-long-random-secret
POSTGRES_PASSWORD=replace-with-a-random-password
CORS_ALLOWED_ORIGINS=https://your-frontend.onrender.com
```

`COMPOSE_FILE` y `COMPOSE_PROFILES` hacen que `docker compose` use los dos
archivos y el perfil `map` sin flags. sslip.io resuelve el nombre a la IP de la
VM sin registrar un dominio.

Primer despliegue:

```bash
git clone https://github.com/WaiotFiuba/backend.git
cd backend
cp .env.oracle.example .env
python3 scripts/download_seed_data.py
docker compose up -d --build
```

Deploy automatico: el workflow entra por SSH con el usuario `deploy`, cuya clave
solo puede ejecutar `/usr/local/bin/waiotfiuba-deploy` (copia de
`deploy/bootstrap.sh`). Ese script deja el codigo en `origin/main`, descartando
cambios locales en archivos versionados, y ejecuta `deploy/waiotfiuba-deploy.sh`,
que descarga los datos semilla y reconstruye los servicios. El `.env` no se toca.
Despues, el workflow espera hasta 5 minutos a que la API responda.

Guardadas en GitHub, en el environment `production`:

```text
ORACLE_SSH_KEY
ORACLE_KNOWN_HOSTS
ORACLE_HOST
```

y la variable publica `ORACLE_API_URL`, con la que el workflow verifica que la API
responda.

Operacion, desde la carpeta del repo en la VM:

```bash
docker compose ps
docker compose logs -f --tail 100 api
docker compose restart api
docker compose down
```

## Checklist antes de mergear `dev` a `main`

- Backend levanta localmente.
- Frontend compila con `npm run build`.
- Migraciones revisadas.
- Mapa carga desde el frontend de Render.
- API responde en la VM.
- CORS permite el dominio del frontend.
- Simulador inicia, pausa, reanuda y frena.
- No hay seeds destructivos automaticos.

## Limitaciones

- La VM es una sola maquina: si se cae, se caen la API, la base y el simulador.
- La base de la VM no tiene backup automatico.
- Si cambia la IP publica de la VM, cambian `API_DOMAIN`, el certificado,
  `VITE_BACKEND_URL` y `CORS_ALLOWED_ORIGINS`.
