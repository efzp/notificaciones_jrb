# Catalogo de entidades y correos: migracion y carga inicial

Esta entrega crea el catalogo en `sqldb-jrb-sabana-dev` y un cargador de linea de comandos. La ampliacion de Functions, reservas y recuperacion diaria se documenta en [TERCEROS_AUTOMATIZACION.md](TERCEROS_AUTOMATIZACION.md); los flujos productores deben incorporar sus avisos al terminar las cargas.

## Modelo

- `ref.entidad_correo`: asociacion unica de entidad, rol y direccion SMTP. Reutiliza `core.entidad` y `mail.direccion`; no modifica nombres ni direcciones existentes.
- `ref.entidad_correo_fuente`: respaldo agregado por instantanea y origen (`JNC` o `JRB_PST`), con casos distintos, observaciones, fechas observadas, hash de referencias y una referencia de muestra.
- `ref.entidad_correo_revision`: historial de decisiones manuales con evidencia y alcance temporal. El cargador nunca inserta ni modifica decisiones.
- `ing.terceros_carga` y `ing.terceros_publicacion`: identificacion de instantaneas y puntero a la publicacion actual.
- `ing.terceros_pendiente`: incidencias agrupadas y asociaciones que necesitan revisar nombres, casos o direcciones.
- `cfg.migracion_terceros`: version y SHA-256 del DDL aplicado.
- `rep.vw_revision_entidad_correo`: comparacion JRB/JNC del corte publicado. La ultima decision registrada se muestra con sus fechas; no se declara aplicable automaticamente a todos los envios.
- `ref.entidad_equivalencia` y `rep.vw_equivalencia_entidad`: agrupaciones propuestas por carga, con nombre original, IDs original/representante/candidato, metodo, puntajes, umbral y margen. No modifican `core.entidad` ni sus relaciones con los casos.

Los roles admitidos son ARL, AFP, EPS, aseguradora, ultimo empleador, remitente y regional. Los pacientes se excluyen en la extraccion y en las restricciones SQL. La cedula se usa solo en memoria para relacionar el asunto con un caso y no se escribe en la instantanea de terceros.

## Reglas de la carga inicial

1. JNC se consulta en modo de solo lectura. Se utilizan `calificacion_sistema_envio_entidad`, `notificacion_esperada` y sus casos asociados. Los cruces exactos con un registro de correo certificado aportan un conteo adicional; no se interpretan como autorizaciones.
2. Se separan listas de direcciones. Se conserva la parte local y se normaliza el dominio; no se corrigen dominios por semejanza ni se consultan DNS. Direcciones mal formadas quedan como incidencias.
3. La version inicial `terceros-v1` resolvia solo nombres normalizados unicos. La version actual `terceros-v2` agrupa por rol, expresiones regulares y proximidad con las reglas documentadas abajo. Toda equivalencia sigue siendo una propuesta operativa, no una identificacion legal por NIT. Las equivalencias ausentes o ambiguas permanecen pendientes.
4. Solo se leen partes del consolidado publicado y PST con carga completa vigente. La regla inicial del PST exige clase `IPM.Note` o subclase, remitente mostrado `Notificaciones SalaN`, rol institucional explicito en el asunto y un unico caso/entidad para la identificacion encontrada. Es una inferencia de uso observado basada en nombre de emisor y asunto, no autenticacion del remitente. Reportes, respuestas, borradores y asuntos de notificacion al paciente se excluyen. Los casos con dos ARL del mismo rol se dejan pendientes si no hay una atribucion inequivoca.
5. Se consideran destinatarios directos (`Para`), combinando MAPI/cabecera sin duplicar direcciones. CC y CCO no se cuentan como envio dirigido a la entidad.
6. Reenvios y fuentes JNC que repiten el mismo caso no incrementan el numero de casos distintos. La clave JNC utiliza identificacion, radicado y audiencia, conservando solo su hash en memoria. Si falta identificacion o radicado, el registro queda pendiente en lugar de inventar un caso distinto.
7. Las fechas imposibles o posteriores al dia de captura se senalan y se excluyen de los extremos temporales. Las asociaciones siguen siendo observadas; ninguna fecha equivale a vigencia autorizada. Las fechas JNC pueden proceder del reporte de notificacion o de envio segun la fuente; si faltan, permanecen nulas.
8. El denominador de frecuencia es el conjunto de casos de esa entidad/rol/origen con una direccion utilizable y resuelta. Los porcentajes de varios contactos pueden sumar mas de 100 si un mismo caso se dirige a varios contactos. No representan la totalidad de casos del consolidado.

JRB y JNC mantienen sus estadisticas separadas. La presencia posterior en JNC no acredita retrospectivamente la validez del contacto JRB.

## Ejecutar desde la raiz del repositorio

Requisitos: Python 3.12, dependencias de `03_trabajo/function_sabana/requirements.txt`, ODBC Driver 18 y Azure CLI autenticado en el tenant del proyecto. Las bases y servidores estan fijados en `terceros/database.py`; JNC no admite operaciones de escritura desde este componente. El lector exige `ALLOW_SNAPSHOT_ISOLATION` para obtener una captura consistente dentro de cada base. Las lecturas de las dos bases son independientes y se registran sus limites de tiempo; no se afirma una transaccion distribuida.

```powershell
# Vista previa: no conecta ni crea objetos.
python .\03_trabajo\function_sabana\tools\cargar_terceros.py migrar

# Aplicar migraciones 09 y 10. Reejecutar verifica sus huellas y no las repite.
python .\03_trabajo\function_sabana\tools\cargar_terceros.py migrar --aplicar

# Capturar datos, sin escribir en SQL. Usar un nombre nuevo para cada captura.
python .\03_trabajo\function_sabana\tools\cargar_terceros.py capturar .\03_trabajo\catalogo_terceros\resultados\corte.json.gz --umbral 95 --margen 5

# Simulacion de la carga desde esa instantanea: sin escrituras SQL.
python .\03_trabajo\function_sabana\tools\cargar_terceros.py cargar .\03_trabajo\catalogo_terceros\resultados\corte.json.gz

# Publicar la instantanea de forma atomica.
python .\03_trabajo\function_sabana\tools\cargar_terceros.py cargar .\03_trabajo\catalogo_terceros\resultados\corte.json.gz --aplicar
```

La instantanea contiene datos de contacto y debe permanecer en una carpeta `resultados`, excluida de Git. No se sobrescribe. Su SHA-256 identifica contenido y version de regla; no es una firma de autenticidad. La misma instantanea se puede volver a cargar: devuelve `reutilizada=true`, conserva los identificadores y no duplica filas. Una captura antigua distinta no desplaza una publicacion mas reciente. Las capturas y respaldos previos se conservan localmente para reproducir los resultados; no se suben a GitHub ni al paquete de Azure.

La migracion se ejecuta en una transaccion y registra su checksum. Una modificacion posterior del DDL aplicado exige una nueva migracion, no editar la version 09. La carga utiliza un bloqueo de aplicacion y una transaccion para crear direcciones nuevas, asociaciones, respaldos y pendientes; actualiza el puntero de publicacion al finalizar. Un fallo revierte toda la carga. JNC dispone de reintentos acotados para una base serverless temporalmente no disponible.

## Consultas de revision

```sql
SELECT TOP (100) *
FROM rep.vw_revision_entidad_correo
ORDER BY casos_jrb DESC, casos_jnc DESC, entidad, correo;

SELECT p.origen,p.codigo,COUNT_BIG(*) AS grupos,SUM(CONVERT(bigint,p.ocurrencias)) AS ocurrencias
FROM ing.terceros_pendiente p
JOIN ing.terceros_publicacion c ON c.id_carga_terceros=p.id_carga_terceros
GROUP BY p.origen,p.codigo
ORDER BY grupos DESC;

SELECT * FROM ing.terceros_carga ORDER BY id_carga_terceros DESC;
```

La publicacion puede presentar pocas coincidencias JRB/JNC por las reglas conservadoras de nombres y rol. Es una limitacion visible del catalogo. Los pendientes no se convierten en asociaciones aprobadas ni se ocultan para aumentar la cobertura.

## Agrupacion por expresiones regulares y proximidad

`terceros/matching.py` aplica reglas generales, sin una lista de excepciones por empresa:

- Mayusculas, acentos, puntuacion, espacios y orden de palabras.
- Variantes `S.A.`/`S A`/`SA`, `S.A.S.`/`SAS`, `LIMITADA`/`LTDA` y expansiones de las formas societarias.
- Variantes de siglas como `E.P.S.`/`EPS`, `A.R.L.`/`ARL`, `A.F.P.`/`AFP` y descripciones completas del rol.
- Separacion del rol y de palabras gramaticales al construir la firma comparable. Las formas societarias se conservan como restricciones: SA y SAS explicitas incompatibles no se fusionan.
- Se preservan calificadores como Vida, Generales, Grupo, Fundacion y Cooperativa; no se eliminan ubicaciones ni numeros para forzar coincidencias.
- El indice usa los roles observados en el consolidado. El representante de cada grupo es el menor ID original, para una seleccion determinista. Los nombres y relaciones originales siguen intactos.

Si las firmas completas coinciden y pasan los controles de compatibilidad, el puntaje es 100. En otro caso, la proximidad es `0.90 * ratio(tokens ordenados) + 0.10 * ratio(texto normalizado)`, usando RapidFuzz 3.14.6. No se usa `token_set_ratio`: un subconjunto del nombre no debe recibir automaticamente 100.

Umbrales operativos:

- **95 o mas** y **al menos 5 puntos** sobre el segundo grupo candidato: agrupacion automatica propuesta.
- **85 a menos de 95**: sugerencia para revision, sin asignacion automatica.
- Dos candidatos demasiado cercanos: ambiguo.
- Menos de 85, nombres de relleno o incompatibilidades: sin equivalencia automatica.

El puntaje no es una probabilidad de identidad ni un porcentaje de precision. Los nombres cortos sin firma exacta no se unifican por aproximacion. La agrupacion verifica el umbral contra todos los miembros del grupo: A parecido a B y B parecido a C no basta para agrupar A con C. Un nombre sin forma societaria no sirve de puente entre SA y SAS incompatibles.

La herramienta `tools/evaluar_proximidad_terceros.py` compara umbrales 93/95/97 sobre un perfil local de nombres y roles. Sus resultados son cobertura operacional, no una evaluacion de precision con etiquetas verificadas. El perfil y la calibracion se conservan en `03_trabajo/catalogo_terceros/resultados`, fuera de Git.

```sql
SELECT TOP (100) origen,tipo_parte,nombre_origen,entidad_representante,
       entidad_candidata,metodo,puntaje,segundo_puntaje,umbral,margen
FROM rep.vw_equivalencia_entidad
WHERE metodo IN ('REGEX','PROXIMIDAD','REVISION','AMBIGUA')
ORDER BY metodo,puntaje DESC,nombre_origen;
```

Las capturas v1 siguen siendo legibles y reejecutables. Una nueva agrupacion crea otra publicacion sin borrar respaldos anteriores. Las decisiones manuales previas permanecen vinculadas a sus asociaciones originales; no se propagan automaticamente si cambia el representante de una agrupacion.

## Permisos y despliegue

La migracion necesita una identidad con permisos DDL en JRB. Crea `rol_carga_terceros` y `rol_revision_terceros` sin asignarlos a usuarios. La carga inicial se ejecuta con la identidad Entra de Azure CLI. Para automatizar despues en Functions se requiere provisionar lectura de las consultas JNC/JRB con una identidad de ejecucion, y asignar el rol de escritura del catalogo en JRB. El rol de revision permite insertar decisiones, no actualizarlas ni borrarlas. La identidad de GitHub no recibe permisos SQL en esta entrega.

El workflow ejecuta las pruebas de terceros y de proximidad, pero no aplica DDL ni ejecuta cargas. El paquete incluye ahora el trabajador asincrono y las huellas de los DDL 09/10. La CLI administrativa sigue disponible. La activacion y el enlace de los productores se describen en TERCEROS_AUTOMATIZACION.md; las aprobaciones manuales de equivalencias siguen siendo una etapa posterior.

## Pruebas

La suite local comprueba normalizacion, exclusion de pacientes, casos distintos, huellas reproducibles y asociaciones ambiguas. La integracion SQL esta desactivada en CI. Para comprobar una publicacion ya cargada, desde `03_trabajo/function_sabana`:

```powershell
$env:TERCEROS_SQL_INSTANTANEA = (Resolve-Path '..\catalogo_terceros\resultados\corte.json.gz').Path
python -m unittest discover -s tests -p test_terceros_sql.py -v
Remove-Item Env:TERCEROS_SQL_INSTANTANEA
```

La integracion verifica reejecucion sin duplicados, provoca un fallo antes de publicar una instantanea de prueba y confirma rollback completo. Tambien intenta insertar el rol paciente dentro de una transaccion revertida para comprobar la restriccion SQL. No conserva datos sinteticos ni altera decisiones de revision.

## Reversion del filtro de dominios

El cambio `terceros-v3` de frecuencia de dominios y mapeo inverso fue revertido por solicitud del usuario. El cargador vuelve a generar `terceros-v2`, con regex/proximidad y sin filtro de frecuencia por dominio. Se restauro la publicacion 4: 1.393 asociaciones, 764 dominios y 12.568 grupos pendientes.

La publicacion 6, sus agregados y la migracion 11 permanecen como historial inactivo; el ejecutor vigente aplica solo las migraciones 09 y 10. `sql/revertir_publicacion_terceros_v3.sql` documenta la restauracion protegida por huellas y bloqueo transaccional. Las vistas de dominio no muestran filas para la publicacion 4.

## Tabla de control para automatizacion

`sql/12_control_actualizacion_terceros.sql` prepara `ing.terceros_actualizacion` con una fila por revision publicada de carga: JNC/LOTE, JRB/EXCEL o JRB/PST. El script se ejecuta directamente en JRB y no esta integrado en el ejecutor de migraciones. La Function de automatizacion utiliza esa tabla; requiere instalarla previamente. Las pruebas de desarrollo se ejecutaron en el motor con rollback.

La clave unica de origen, tipo, carga y hash de revision elimina avisos repetidos. Una correccion, reemplazo o inactivacion debe producir una revision distinta. El aviso se registra despues de publicar correctamente el origen. Cada fila contiene solo metadatos de control; no contiene Excel, PST, pacientes ni correos.

El valor predeterminado de disponibilidad es diez minutos despues del ultimo aviso nuevo pendiente. El trabajador agrupa las filas elegibles con el mismo `id_ejecucion`; los avisos que lleguen mientras trabaja permanecen pendientes. Los estados son PENDIENTE, EN_PROCESO, REINTENTO, COMPLETADA y AGOTADA. Un trabajo completado referencia una carga del maestro, incluida una carga reutilizada si el contenido no cambio.

La tabla no garantiza por si sola una unica ejecucion global. `terceros/control.py` reclama trabajos en una transaccion con bloqueo de aplicacion, comprueba reservas activas, renueva su vencimiento y recupera reservas expiradas. El cargador verifica la reserva dentro de la transaccion de publicacion e impide publicar con una reserva perdida. La publicacion y la finalizacion de las solicitudes se confirman atomicamente. `version_fila` apoya el control de concurrencia local; no detecta cambios en las bases de origen.
