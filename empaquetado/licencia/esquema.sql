-- Tabla de licencias (Supabase / Postgres).
-- Ver specs\distribucion_instalador.md S6.1.

create table if not exists licencias (
  clave       text primary key,                  -- la que se entrega a mano
  vence       timestamptz not null,
  maquina     text,                              -- se fija en el primer uso
  activa      boolean not null default true,
  nota        text,                              -- "para X", a modo de memo
  creada      timestamptz not null default now(),
  ultimo_uso  timestamptz
);

-- RLS prendido y SIN ninguna policy: asi la tabla queda inaccesible con
-- la anon key (la que viaja dentro del paquete). La unica que la lee y
-- escribe es la Edge Function, que usa la service_role y salta RLS.
-- Sin esto, cualquiera con el .exe podria listar todas las claves.
alter table licencias enable row level security;

-- Para ver de un vistazo que hay emitido.
create or replace view licencias_resumen as
select clave,
       nota,
       activa,
       vence,
       (vence < now()) as vencida,
       (maquina is not null) as atada,
       ultimo_uso
from licencias
order by creada desc;

-- Emitir una licencia a mano:
--
--   insert into licencias (clave, vence, nota)
--   values ('NIMBO-ABCD-1234-EFGH', now() + interval '90 days', 'para X');
--
-- Renovar:
--
--   update licencias set vence = now() + interval '90 days' where clave = '...';
--
-- Cortar el acceso ya (el token que tenga puesto sigue valiendo hasta que
-- vence -- por eso la vida del token es de dias y no de meses):
--
--   update licencias set activa = false where clave = '...';
--
-- Mudar a otra maquina (soltar la atadura):
--
--   update licencias set maquina = null where clave = '...';
