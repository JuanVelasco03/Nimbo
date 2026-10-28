# Licencia: puesta en marcha

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
supabase secrets set LICENCIA_PRIV_D=<la privada> LICENCIA_PUB_X=<la publica>
```

`--no-verify-jwt` es necesario: quien llama es Nimbo con la anon key, no un
usuario logueado.

`esquema.sql` deja RLS **prendido y sin ninguna policy**. Eso es lo que hace
que la anon key que viaja dentro del paquete no sirva para leer la tabla de
claves; la única que la toca es la función, con la `service_role`.

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
