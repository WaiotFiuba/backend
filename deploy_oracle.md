# Deploy en Oracle Cloud (VM Always Free)

Registro de cómo está armado el servidor de producción y de cada cambio que se le hace. **Cada cambio nuevo se agrega al registro (sección 8)**, con qué se hizo, dónde y cómo deshacerlo.

**Estado al 2026-10-09:** la API, PostGIS, el simulador y Caddy corren en la VM de Oracle. La API responde por HTTPS en `https://144-22-50-249.sslip.io`. Falta crear la tabla `users` y apuntar el front de Vercel a la URL nueva (ver sección 9).

## 1. Acceso

| | |
|---|---|
| URL de la API | `https://144-22-50-249.sslip.io` |
| IP pública | `144.22.50.249` |
| SSH (administrador) | `ssh -i ~/.ssh/oracle_waiotfiuba ubuntu@144.22.50.249` |
| SSH (equipo) | `ssh <usuario>@144.22.50.249`, con la clave de cada uno |
| Consola de Oracle | https://cloud.oracle.com, región **Chile Central (Santiago)** |

Usuarios de la VM: `ubuntu` (administrador, con `sudo`) y `valentin` (sin `sudo`). Los dos están en el grupo `docker`, que equivale a administrador de la VM.

## 2. Cuenta de Oracle

- **Región de origen:** Chile Central (Santiago), AD `fxgg:SA-SANTIAGO-1-AD-1`. Lo Always Free existe solo en esta región.
- **Tipo de cuenta:** Pay As You Go (desde el 2026-10-08). Lo Always Free no se cobra; se cobraría lo que lo supere.
- **Cuota `solo-always-free`** (compartment raíz): Oracle rechaza crear cualquier cosa por encima de esto.
  ```
  zero compute-core quotas in tenancy
  set compute-core quota standard-a1-core-count to 2 in tenancy
  set compute-core quota standard-a1-core-regional-count to 2 in tenancy
  set compute-memory quota standard-a1-memory-count to 12 in tenancy
  set compute-memory quota standard-a1-memory-regional-count to 12 in tenancy
  set block-storage quota total-storage-gb to 200 in tenancy
  set block-storage quota backup-count to 5 in tenancy
  ```
  Ojo: los cambios de cuota tardan unos minutos en aplicarse al crear recursos.
- **Presupuesto `alerta-cero`:** 1 USD por mes, con dos reglas que avisan por mail a `joaalvarez@fi.uba.ar`, `joalv4@gmail.com` y `joaquinlvarez@gmail.com`:
  - `alertrule20261008141840`: gasto **previsto** del 1 % (0,01 USD);
  - `gasto-real`: gasto **real** del 1 %.
- **Para no pagar nunca:** no activar políticas automáticas de backup de discos, no subir el rendimiento de los discos por encima de Balanced, no crear nada fuera de Santiago. Revisar Cost Analysis una vez por semana.

## 3. Recursos

| Recurso | Nombre | Detalle |
|---|---|---|
| VM | `waiotfiuba-prod` | `VM.Standard.A1.Flex`, **2 OCPU y 12 GB** (todo el cupo gratuito), Ubuntu 24.04 aarch64, build 2026.09.18-0 |
| Disco de arranque | `waiotfiuba-prod (Boot Volume)` | 100 GB, Balanced (10 VPU) |
| Disco de datos | `waiotfiuba-data` | 100 GB, Balanced (10 VPU), paravirtualizado en `/dev/oracleoci/oraclevdb`, sin política de backup |
| Red | `waiotfiuba-vcn` | Creada con el asistente "VCN with Internet Connectivity"; la VM está en `public subnet-waiotfiuba-vcn` |
| Security List | `Default Security List for waiotfiuba-vcn` | Entrada: TCP 22, 80 y 443 desde `0.0.0.0/0`; ICMP |

Total de discos: 200 de 200 GB gratuitos.

## 4. La VM

- **Disco de datos:** ext4 con etiqueta `waiotfiuba-data`, montado en `/data`. En `/etc/fstab` por UUID con `nofail` (si el disco no está, la VM arranca igual):
  ```
  UUID=3ceeee3f-42ff-4877-bbd4-b7fb2618cc6a /data ext4 defaults,nofail 0 2
  ```
- **Firewall (`iptables`):** la imagen de Oracle rechaza todo salvo SSH. Se agregaron `ACCEPT` para TCP 80 y 443 antes del `REJECT` de `INPUT`, en vivo y en `/etc/iptables/rules.v4` (copia del original en `/etc/iptables/rules.v4.original`). No usar `netfilter-persistent save`: guardaría también las reglas que arma Docker.
- **Docker:** Docker Engine 29.9 y Compose 5.6, del repositorio oficial de Docker. `/etc/docker/daemon.json`:
  ```json
  {"data-root": "/data/docker", "log-driver": "json-file", "log-opts": {"max-size": "20m", "max-file": "5"}}
  ```
  Volúmenes (incluida la base) y contenedores quedan en `/data/docker`. Las **imágenes** quedan en `/var/lib/containerd`, en el disco de arranque, porque Docker 29 usa el almacenamiento de imágenes de containerd.

## 5. La aplicación

- **Código:** `/data/app/backend`, clon de la rama `dev` por HTTPS (el repo es público). Dueño `ubuntu`, grupo `docker`, con escritura para el grupo.
- **Datos semilla:** bajados con `scripts/download_seed_data.py`. Más tres archivos que el release no trae, copiados a mano: `barrios.geojson`, `calles.geojson` y `poblacion_barrios.csv`. Sin `container_types.json`: el seed usa los tipos predeterminados, que incluyen el de los contenedores verdes.
- **Servicios** (`docker compose ps`): `postgis`, `api`, `simulator` y `caddy`, todos con `restart: unless-stopped`.
- **Puertos publicados en la VM:** solo 80 y 443 (Caddy). La API (8000) y PostGIS (5432) solo son accesibles dentro de la red de Docker.
- **Base:** `waiotfiuba`, usuario `waiotfiuba`. Primer seed: 28.267 contenedores negros y 4.369 verdes, en 26.214 sitios, y la población de los 48 barrios.

### Archivos de producción (solo en la VM, fuera del repo)

- **`docker-compose.prod.yml`:** se aplica encima de `docker-compose.yml`.
  - La API y el simulador usan el código de la imagen, sin volúmenes de código y sin `--reload`.
  - La API arranca con `--proxy-headers`, porque está detrás de Caddy.
  - La API y PostGIS no publican puertos.
  - Suma el servicio `caddy` (imagen `caddy:2`), con volúmenes `caddy_data` y `caddy_config`.
- **`deploy/Caddyfile`:** HTTPS automático con Let's Encrypt para `API_DOMAIN`, con compresión, reenviando a `api:8000`.
- **`.env`:** generado en la VM con secretos al azar (`openssl rand`); permisos `640`, grupo `docker`. Además de los valores de `.env.example`:
  - `COMPOSE_FILE=docker-compose.yml:docker-compose.prod.yml` y `COMPOSE_PROFILES=map`, así `docker compose` usa los dos archivos y el perfil `map` sin flags;
  - `API_DOMAIN=144-22-50-249.sslip.io`. sslip.io es un DNS público que resuelve ese nombre a la IP, sin registrarse;
  - `APP_ENV=production`, `AUTO_CREATE_DB=false`;
  - `CORS_ALLOWED_ORIGINS=*`, abierto por ahora;
  - `SIMULATOR_BATCH_SIZE=10000`;
  - `POSTGRES_PASSWORD`, `SECRET_KEY`, `DATABASE_URL` y `MAP_DATABASE_URL`, apuntando a `postgis:5432/waiotfiuba`.

## 6. Operación

Todo desde `/data/app/backend`:

```bash
docker compose ps                      # estado de los servicios
docker compose logs -f --tail 100 api  # logs de un servicio
docker compose restart api             # reiniciar un servicio
docker compose up -d --build           # desplegar después de un git pull
docker compose down                    # bajar todo (no borra la base ni las imágenes)
```

Desplegar una versión nueva:

```bash
cd /data/app/backend
git pull
docker compose up -d --build
```

Al arrancar, la API corre las migraciones y el seed. El seed no duplica contenedores que ya existen.

Deploy automático: al llegar un push a `main`, GitHub Actions corre los tests y después entra por SSH como `deploy`, lo que ejecuta `/usr/local/bin/waiotfiuba-deploy`. Para dispararlo a mano: Actions → Release production → Run workflow. Ojo: el script deja la VM en `main` y descarta cambios locales en archivos versionados.

Agregar un usuario a la VM (con su clave **pública**):

```bash
sudo adduser --disabled-password --gecos "Nombre Apellido" usuario
sudo install -d -m 700 -o usuario -g usuario /home/usuario/.ssh
echo "<clave pública>" | sudo tee /home/usuario/.ssh/authorized_keys
sudo chown usuario:usuario /home/usuario/.ssh/authorized_keys
sudo chmod 600 /home/usuario/.ssh/authorized_keys
sudo usermod -aG docker usuario
```

Sacarlo: `sudo deluser --remove-home usuario`.

## 7. Cómo se consiguió la VM

Oracle no tenía capacidad para A1 en Santiago. La VM se creó con `~/oracle-vm/crear_vm.sh` (en la máquina de Joaquín), que reintenta con la CLI de Oracle hasta que hay lugar: salió en el intento 149, con 1 OCPU y 6 GB, después de pasar la cuenta a Pay As You Go. Después se agrandó a 2 OCPU y 12 GB desde la consola.

## 8. Registro de cambios

| Fecha | Qué | Dónde | Cómo deshacerlo |
|---|---|---|---|
| 2026-10-07 | Cuenta Always Free en la región Chile Central | Oracle | — |
| 2026-10-08 | Cuenta pasada a Pay As You Go | Oracle, Billing | No se puede volver a Free Tier |
| 2026-10-08 | Cuota `solo-always-free` (núcleos y memoria A1; después discos y backups) | Oracle, Quota Policies | Editar o borrar la política |
| 2026-10-08 | Presupuesto `alerta-cero` con regla de gasto previsto | Oracle, Budgets | Borrar el presupuesto |
| 2026-10-08 | VCN `waiotfiuba-vcn` con el asistente | Oracle, Networking | Borrarla (antes, la VM) |
| 2026-10-08 | VM `waiotfiuba-prod` (1 OCPU, 6 GB, disco de 100 GB) | Oracle, Compute | Terminate |
| 2026-10-08 | VM agrandada a 2 OCPU y 12 GB | Oracle, Compute → Edit shape | Volver a editar el shape |
| 2026-10-08 | Disco `waiotfiuba-data` de 100 GB, conectado como `/dev/oracleoci/oraclevdb` | Oracle, Block Volumes | Detach y Terminate (se pierden los datos) |
| 2026-10-08 | Disco formateado (ext4) y montado en `/data`, con línea en `/etc/fstab` | VM | Sacar la línea de `/etc/fstab` y desmontar |
| 2026-10-08 | Usuario `valentin` con su clave SSH | VM | `sudo deluser --remove-home valentin` |
| 2026-10-08 | Regla `gasto-real` en el presupuesto | Oracle, Budgets | Borrar la regla |
| 2026-10-08 | Docker 29.9 y Compose 5.6, con `/etc/docker/daemon.json`; `ubuntu` y `valentin` en el grupo `docker` | VM | `sudo apt purge docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin` y borrar `/etc/docker`, `/data/docker` y `/var/lib/containerd` |
| 2026-10-08 | Puertos 80 y 443 abiertos desde `0.0.0.0/0` | Oracle, Security List de la VCN | Borrar las dos reglas de entrada |
| 2026-10-08 | Puertos 80 y 443 abiertos en `iptables` | VM, `/etc/iptables/rules.v4` | Restaurar `/etc/iptables/rules.v4.original` y reiniciar |
| 2026-10-08 | Clon de `dev` en `/data/app/backend` y datos semilla | VM | Borrar `/data/app` |
| 2026-10-08 | Copia de `barrios.geojson`, `calles.geojson` y `poblacion_barrios.csv` | VM, `datos/digital_twin/` | Borrar los archivos |
| 2026-10-08 | `docker-compose.prod.yml`, `deploy/Caddyfile` y `.env` de producción | VM, `/data/app/backend` | Borrar los archivos |
| 2026-10-08 | Primer `docker compose up -d --build`: imágenes, base sembrada y certificado de Let's Encrypt | VM | `docker compose down -v` (borra también la base) |
| 2026-10-09 | Usuario `deploy` (sin `sudo`, en el grupo `docker`, con `safe.directory` de git para `/data/app/backend`) | VM | `sudo deluser --remove-home deploy` |
| 2026-10-09 | Script `/usr/local/bin/waiotfiuba-deploy`: pone el repo en `origin/main`, baja datos semilla y hace `docker compose up -d --build` | VM | `sudo rm /usr/local/bin/waiotfiuba-deploy` |
| 2026-10-09 | Clave de GitHub Actions autorizada para `deploy`, limitada a correr el script (`restrict,command=`) | VM, `/home/deploy/.ssh/authorized_keys` | Borrar la línea |
| 2026-10-09 | Workflow `release-production.yml`: tras los tests, deploy en paralelo a Render y a Oracle (branch `update-deployment`, sin mergear) | Repo | Revertir el commit |
| 2026-10-09 | Secrets `ORACLE_HOST`, `ORACLE_SSH_KEY`, `ORACLE_KNOWN_HOSTS` y variable `ORACLE_API_URL` | GitHub, environment `production` | Borrarlos |

Los cambios del 2026-10-08 desde Docker en adelante los hizo Claude en una sola tanda, sin revisión previa paso a paso. Quedaron revisados con este registro.

## 9. Pendiente

- [ ] Crear la tabla `users` (en producción no se crea sola, por `AUTO_CREATE_DB=false`).
- [ ] Cambiar `VITE_BACKEND_URL` en Vercel a `https://144-22-50-249.sslip.io`, y restringir `CORS_ALLOWED_ORIGINS` a la URL del front.
- [ ] Backup diario de la base con `pg_dump`, guardado fuera de la VM.
- [ ] Sumar `docker-compose.prod.yml`, `deploy/Caddyfile` y una plantilla del `.env` (sin secretos) al repo.
- [ ] Agregar al resto del equipo a la VM.
- [ ] Deploy automático: falta mergear `update-deployment` y que `dev` llegue a `main` (con el test `test_container_arrays_apply_recyclable_factor` arreglado). Hasta entonces la VM sigue en `dev`.
- [ ] Decidir qué hacer con lo que quedó del intento con Azure: el servidor PostgreSQL `waiotfiuba-db` (Chile Central, Azure for Students, gratis por 12 meses), con la base `waiotfiuba` a medio sembrar, sin uso.
- [ ] Avisarle a Lucas: el manifiesto de `download_seed_data.py` no incluye `barrios.geojson`, `calles.geojson` ni `poblacion_barrios.csv`, y `db/import_geojson.sh` con `-skipfailures` es muy lento contra una base remota.
