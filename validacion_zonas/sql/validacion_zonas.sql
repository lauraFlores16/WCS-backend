-- ============================================================================
-- VALIDACIÓN MULTIZONA — tablas nuevas (ejecutar UNA vez en Supabase → SQL Editor)
-- ============================================================================
-- Todo lo que antes se hacía en la terminal (p1, p2, p3) ahora lo lanza el
-- dashboard y el backend lo guarda aquí. Los datos voluminosos (focos, grid,
-- cicatriz, insumos) van comprimidos con gzip en base64 (columnas *_gz): ocupan
-- ~10 veces menos que el JSON plano.
--
-- Tamaño orientativo: un municipio-año ≈ 0,1–1 MB; un paquete ≈ 0,2–1 MB;
-- un año de MapBiomas agregado a 500 m para toda Bolivia ≈ 1–3 MB.
-- ============================================================================

-- Cicatrices anuales de MapBiomas Fuego, agregadas a la rejilla global de 500 m
create table if not exists public.validacion_mapbiomas (
  anio integer primary key,
  npz_b64 text not null,                    -- numpy .npz (validos, quemados, i0, j0) en base64
  fuente text,
  cobertura jsonb,                          -- bbox con dato, celdas con dato, km² quemados
  subido_por text,
  subido_en timestamptz not null default now()
);

-- Focos FIRMS verificados y eventos de un municipio en un año (lo que hacía p1)
create table if not exists public.validacion_zonas (
  id text primary key,                      -- '<municipio_id>-<año>'
  municipio_id text not null,
  anio integer not null,
  municipio jsonb not null,
  resumen jsonb not null,
  eventos jsonb not null,
  focos_gz text not null,                   -- CSV gzip+base64
  limite jsonb,
  creado_por text,
  creado_en timestamptz not null default now(),
  unique (municipio_id, anio)
);

-- Partición calibración / validación. Un municipio, un rol, para siempre.
create table if not exists public.validacion_particion (
  municipio_id text primary key,
  rol text not null check (rol in ('calibracion', 'validacion')),
  asignado_por text,
  asignado_en timestamptz not null default now()
);

create or replace function public.validacion_rol_inmutable() returns trigger
language plpgsql as $$
begin
  if new.rol is distinct from old.rol then
    raise exception 'El rol de % ya es % y no se puede cambiar: invalidaría la validación externa',
      old.municipio_id, old.rol;
  end if;
  return new;
end $$;

drop trigger if exists validacion_rol_inmutable on public.validacion_particion;
create trigger validacion_rol_inmutable before update on public.validacion_particion
  for each row execute function public.validacion_rol_inmutable();

-- Paquetes de validación de un evento (lo que hacían p2 y p3)
create table if not exists public.validacion_paquetes (
  id text primary key,                      -- '<municipio_id>-<año>-<evento>'
  zona_id text not null references public.validacion_zonas(id) on delete cascade,
  municipio_id text not null,
  anio integer not null,
  evento_id text not null,
  rol text not null check (rol in ('calibracion', 'validacion')),
  paquete jsonb not null,                   -- metadatos (verificación, ventana, fuentes…)
  evento jsonb not null,
  focos_iniciales jsonb not null,
  grid_gz text not null,
  observado_gz text not null,
  insumos_gz text not null,
  oficial jsonb,                            -- métricas de la validación oficial (30 rep.)
  resumen_oficial jsonb,
  repeticiones jsonb,
  creado_por text,
  creado_en timestamptz not null default now()
);
create index if not exists validacion_paquetes_zona on public.validacion_paquetes (zona_id);

-- Trabajos en segundo plano (descargas y corridas lanzadas desde el dashboard)
create table if not exists public.validacion_trabajos (
  id text primary key,
  tipo text not null,                       -- 'zona' | 'paquete' | 'oficial' | 'mapbiomas'
  objetivo text,
  estado text not null default 'en_cola',   -- en_cola | ejecutando | terminado | error
  progreso double precision not null default 0,
  mensaje text,
  log jsonb,
  resultado jsonb,
  error text,
  usuario text,
  creado_en timestamptz not null default now(),
  actualizado_en timestamptz not null default now()
);

-- El backend usa la service_role key (omite RLS). Se activa RLS sin políticas
-- para que nadie más pueda leerlas con la clave pública.
alter table public.validacion_mapbiomas enable row level security;
alter table public.validacion_zonas enable row level security;
alter table public.validacion_particion enable row level security;
alter table public.validacion_paquetes enable row level security;
alter table public.validacion_trabajos enable row level security;

-- Particiones de partida: Apolo calibra, Rurrenabaque valida.
insert into public.validacion_particion (municipio_id, rol, asignado_por) values
  ('apolo-la-paz', 'calibracion', 'inicial'),
  ('puerto-menor-de-rurrenabaque-beni', 'validacion', 'inicial')
on conflict (municipio_id) do nothing;
