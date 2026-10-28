# Licencia: puesta en marcha

> **Ya está desplegado** (28/10/2026). Proyecto de Supabase **Nimbo Licencias**
> (`oudbzuvcyzpzcuophhvd`, us-east-2): tabla `licencias`, tabla
> `licencia_claves`, vista `licencias_resumen` y función `validar` con
> `verify_jwt=false`. La URL, la anon key y la clave pública quedaron en
> `proyecto.local.json`, y el par de claves en `claves.local.json` — los dos
> gitignorados. Para armar el paquete con licencia alcanza con
> `construir.py --licencia`.
>
> Probado contra el despliegue real: clave válida → token firmado que el
> cliente verifica; clave inventada, cortada (`activa=false`), vencida y
> desde otra máquina → rechazadas; y ni la anon key ni la publishable key
> pueden leer ninguna de las tres tablas.
>
> Lo que sigue es cómo se hace desde cero (o cómo rotar).

Implementa `specs\distribucion_instalador.md` §6. Tres piezas:

| Pieza | Dónde corre |
|---|---|
| `claves.py` | acá, una vez (genera el par Ed25519) |
| `esquema.sql` + `funcion\index.ts` | Supabase |
| `nimbo\licencia.py` + `nimbo\firma.py` | en la máquina del usuario |

El cliente **verifica** la firma; nunca le cree a un `{"ok": true}`.

## 1. Generar el par de claves

```bat
python empaquetado\licencia\claves.py --generar
```

Imprime la pública y la privada. La **privada va solo a la Edge Function**,
como variable de entorno. No va a git, no va al paquete, no se guarda en el
repo. Guardala en un gestor de contraseñas: si se pierde hay que rotar el
par, y rotar obliga a reinstalar en el otro equipo.

## 2. Supabase

```bat
rem la tabla (SQL Editor, o supabase db push)
rem   -> empaquetado\licencia\esquema.sql

rem la funcion
supabase functions deploy validar --no-verify-jwt
```

`--no-verify-jwt` es necesario: quien llama es Nimbo con la anon key, no un
usuario logueado.

`esquema.sql` deja RLS **prendido y sin ninguna policy**. Eso es lo que hace
que la anon key que viaja dentro del paquete no sirva para leer la tabla de
claves; la única que la toca es la función, con la `service_role`.

### Dónde vive la clave privada

Dos caminos, y la función prefiere el primero:

1. Variables de entorno `LICENCIA_PRIV_D` / `LICENCIA_PUB_X`. Se ponen
   **solo desde el panel** (Edge Functions → validar → Secrets) o con
   `supabase secrets set`; la API de gestión no las expone.
2. La tabla `licencia_claves`, fila `actual`.

El despliegue de hoy usa el camino 2, porque se hizo por API. Mover la clave
al panel después no rompe nada: `parDeClaves()` en `index.ts` mira el entorno
primero. El nivel de protección es el mismo — las dos se leen con la
`service_role` y ninguna con la anon.

## 3. Emitir una licencia

```sql
insert into licencias (clave, vence, nota)
values ('NIMBO-ABCD-1234-EFGH', now() + interval '90 days', 'para X');
```

La clave la inventás vos — no tiene formato obligatorio. `select * from
licencias_resumen;` muestra qué hay emitido, qué venció y qué quedó atado a
un equipo.

## 4. Armar el paquete con licencia

```bat
rem con el backend ya desplegado (toma todo de proyecto.local.json)
python empaquetado\construir.py --descargar --completo --licencia

rem o a mano, si apuntas a otro proyecto
python empaquetado\construir.py --descargar ^
    --clave-publica <la publica> ^
    --url-licencia https://<proyecto>.supabase.co/functions/v1/validar ^
    --anon-licencia <la anon key del proyecto>
```

Eso escribe `nimbo\licencia_publica.json` **dentro del paquete**. Sin ese
archivo, `licencia.activa()` da False y Nimbo arranca sin pedir nada: por eso
la copia de desarrollo nunca se bloquea, y por eso ese archivo está en
`.gitignore`.

## Operación

| Qué querés | Cómo |
|---|---|
| Renovar | `update licencias set vence = now() + interval '90 days' where clave = '...'` |
| Cortar el acceso | `update licencias set activa = false where clave = '...'` |
| Mudar de equipo | `update licencias set maquina = null where clave = '...'` |

**Cortar no es instantáneo.** El token que el cliente ya tiene vale hasta que
vence, y vive 7 días (`DIAS_TOKEN` en `index.ts`). Eso es el precio de la
gracia offline: sin ella, un corte de internet dejaría una noche entera sin
grabar. Si querés que el corte pegue más rápido, bajá `DIAS_TOKEN`; si querés
más tolerancia a cortes, subilo.

## Desbloqueo de emergencia

Si el backend no responde y hay que dejar una instalación andando ya:

```bat
set LICENCIA_PRIV_D=<la privada>
python empaquetado\licencia\claves.py --instalar --clave MANUAL --dias 30
```

Escribe `estado\licencia.json` directo, sin red. Hay que correrlo **en la
máquina del usuario** (el token queda atado a su `MachineGuid`), o pasarle
`--maquina <guid>` desde acá.

## Probar sin desplegar nada

```bat
python empaquetado\licencia\claves.py --probar
```

Hace el camino completo contra `nimbo\licencia.py`: token válido aceptado, y
rechazados el de otra máquina, el vencido, el firmado con otra clave y el de
payload manipulado. Si esto pasa, el cliente está bien; lo único que queda
por probar contra Supabase es la función.

## El techo

La verificación corre en la máquina del usuario, así que un parche al binario
la saltea. Firmar el token sube la vara de "interceptar un HTTP" a "encontrar
y neutralizar la verificación dentro de un binario Nuitka". Para una persona
de confianza alcanza; para un atacante, no. Ver §6.4 de la spec.
