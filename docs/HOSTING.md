# Hosting y despliegue continuo

Este proyecto usa `dev` como rama base de desarrollo y `main` como unica rama
hosteada. Las features salen desde `dev`, vuelven a `dev`, y cuando hay una
version presentable se mergea `dev` a `main`.

Para una primera demo sin costo fijo, la arquitectura recomendada es:

- **Frontend:** Vercel Free.
- **Backend API:** Render Free Web Service.
- **Base de datos:** Supabase Free Postgres con PostGIS.
- **Simulador:** local, apuntando a la API hosteada.

Esta configuracion evita pagar un worker continuo. La contra es que el backend
puede tener cold start en Render Free y el simulador depende de una maquina local.

## Flujo de ramas

```text
feature/*
  -> dev
     -> main
        -> release manual desde GitHub Actions
```

- `main`: estable, presentable y deployable.
- `dev`: integracion de features.
- `feature/*`: trabajo puntual desde `dev`.
- `hosting`: rama temporal para preparar esta configuracion.

## 1. Supabase: base Postgres + PostGIS

Crear un proyecto en Supabase y habilitar PostGIS:

1. Ir a **Database**.
2. Entrar a **Extensions**.
3. Buscar `postgis`.
4. Habilitar la extension.

Tambien se puede habilitar desde SQL:

```sql
CREATE EXTENSION IF NOT EXISTS postgis WITH SCHEMA extensions;
```

En Supabase, copiar el connection string de Postgres y convertirlo al driver async
que usa este backend:

```text
postgresql://USER:PASSWORD@HOST:PORT/postgres
```

queda:

```text
postgresql+asyncpg://USER:PASSWORD@HOST:PORT/postgres
```

Usar esa URL tanto en `DATABASE_URL` como en `MAP_DATABASE_URL` para esta demo.

## 2. Render: backend API

Configuracion sugerida:

- Service type: **Web Service**.
- Runtime: **Docker**.
- Branch: `main`.
- Root directory: vacio si el repo conectado es `backend`; usar `backend` solo si
  Render esta conectado al monorepo raiz.
- Dockerfile path: `./Dockerfile`.
- Start command: dejar vacio para usar el `CMD` del Dockerfile.
- Auto deploy: desactivado.
- Plan: Free para demo.

Variables de entorno:

```env
APP_ENV=production
ENABLE_MAP_DB=true
AUTO_CREATE_DB=false
AUTO_CREATE_MAP_DB=false
DB_ECHO=false
DB_POOL_SIZE=5
DB_MAX_OVERFLOW=0
MAP_DB_POOL_SIZE=5
MAP_DB_MAX_OVERFLOW=0
SECRET_KEY=replace-with-a-long-random-secret
DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/postgres
MAP_DATABASE_URL=postgresql+asyncpg://USER:PASSWORD@HOST:PORT/postgres
CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app
```

Limitaciones esperadas en Render Free:

- La API puede dormir luego de inactividad.
- El primer request despues de dormir puede tardar cerca de un minuto.
- No usar filesystem local para persistencia.
- No correr el simulador como worker continuo en Free.

El deploy de backend lo dispara GitHub Actions despues de correr tests y
migraciones. En Render, copiar el deploy hook desde:

```text
Backend service -> Settings -> Deploy Hook
```

Guardarlo en GitHub como secret:

```text
RENDER_BACKEND_DEPLOY_HOOK_URL
```

## 3. Migraciones y seed

Guardar la URL de Supabase con prefijo `postgresql+asyncpg://` en GitHub como
secret:

```text
PRODUCTION_MAP_DATABASE_URL
```

Para produccion, las migraciones se ejecutan con el workflow manual:

```text
Actions -> Release production backend -> Run workflow
```

Ese workflow:

1. valida que se ejecute desde `main`;
2. instala dependencias;
3. corre tests;
4. ejecuta `alembic upgrade head` contra Supabase;
5. dispara el deploy de Render.

Para ejecutar migraciones manualmente desde una maquina local:

```bash
uv run alembic upgrade head
```

El seed inicial sigue siendo manual:

```bash
uv run python -m app.commands.seed
```

No ejecutar seed automaticamente en cada deploy ni en cada restart.

## 4. Vercel: frontend

Configuracion sugerida:

- Framework: Vite.
- Branch: `main`.
- Root directory: vacio si el repo conectado es `frontend`; usar `frontend` solo si
  Vercel esta conectado al monorepo raiz.
- Build command:

```bash
npm install && npm run build
```

- Output directory:

```text
dist
```

Variables de entorno:

```env
VITE_BACKEND_URL=https://your-backend.onrender.com
VITE_AUTH_DEV_BYPASS=false
VITE_MIN_TICK_SECONDS=16
```

Cuando Vercel entregue la URL final, actualizar en Render:

```env
CORS_ALLOWED_ORIGINS=https://your-frontend.vercel.app
```

## 5. Simulador local apuntando a produccion

El simulador se ejecuta localmente para evitar pagar un worker continuo.

En el backend local, configurar:

```env
SIMULATOR_BACKEND_URL=https://your-backend.onrender.com
SIMULATOR_POLL_SECONDS=2
SIMULATOR_BATCH_SIZE=1000
```

Ejecutar:

```bash
uv run python -m app.digital_twin.synthetic_data.worker
```

El worker queda esperando simulaciones activas en la API hosteada. Cuando el
frontend inicia una simulacion, el worker local genera ticks y envia mediciones a
Render, que las persiste en Supabase.

## Checklist antes de mergear `dev` a `main`

- Backend levanta localmente.
- Frontend compila con `npm run build`.
- Migraciones revisadas.
- Supabase tiene PostGIS habilitado.
- Mapa carga desde Vercel.
- API responde en Render.
- CORS permite el dominio de Vercel.
- Simulador local inicia, pausa, reanuda y frena.
- No hay seeds destructivos automaticos.

## Limitaciones del plan gratis

- Supabase Free tiene limites de espacio, CPU/RAM compartidos y puede pausar por
  inactividad.
- Render Free puede tener cold starts.
- El simulador no queda hosteado 24/7.
- Para una demo 100% independiente de una maquina local, habria que pagar un
  worker o mover todo a un VPS.
