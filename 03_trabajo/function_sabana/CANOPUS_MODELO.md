# Contactos de Canopus y consolidado por cedula

El DDL `sql/15_contactos_canopus_consolidado.sql` implementa la decision del usuario: vincular por cedula, sin exigir reparto ni fecha de audiencia. Ambos datos se conservan como procedencia. Una cedula con varios casos vigentes puede aportar contactos a todos ellos. Para empleadores se mantiene la correspondencia con la entidad, reutilizando las equivalencias vigentes del maestro.

## Objetos

- `ing.canopus_carga`: metadatos y estado de una carga por contenido/parser/archivo de SharePoint. Solo puede haber una carga vigente por archivo. El cargador debe cambiar el puntero `vigente` y publicar en una transaccion.
- `ing.canopus_fila`: cedula y contexto de la fila. No guarda nombre, nacimiento, telefonos, direccion ni datos clinicos. Las filas repetidas por cedula se conservan como observaciones de origen.
- `ref.contacto_reportado`: contactos tanto de CANOPUS como de CONSOLIDADO. Las direcciones validas se referencian en `mail.direccion`; los valores no utilizables se conservan unicamente como incidencias de contacto con su fila/columna.
- `rep.vw_contactos_caso`: contactos candidatos de las partes vigentes, enlazados por cedula y rol. Los contactos de entidad se limitan a la misma entidad o su representante en las equivalencias vigentes.

La tabla comun de contactos sustituye la denominacion preliminar `ing.canopus_contacto`, permitiendo aplicar la prioridad del consolidado sin agregar otra tabla de direcciones.

## Prioridad

1. CONSOLIDADO: 10, si el cargador encontro un correo utilizable para ese rol.
2. CANOPUS: 20, exclusivamente paciente y ultimo empleador.
3. MAESTRO_TERCEROS: 30, para partes institucionales; los pacientes no pertenecen a ese maestro.

La vista conserva todos los correos de la fuente preferida, con su procedencia. Varias observaciones pueden respaldar la misma direccion; no representan varias obligaciones de notificacion. REVISION, DESCARTADO, direcciones no SMTP y cargas no vigentes no bloquean una fuente alternativa utilizable. Un correo candidato no acredita entrega, lectura, respuesta ni vigencia historica.

El esquema actual del consolidado no tiene una columna especifica de correo. `ref.contacto_reportado` permite registrar esos datos cuando el parser los encuentre en una columna identificada como correo. No se infieren direcciones a partir de documentos o guias. El futuro cargador debe validar el formato y la correspondencia antes de marcar UTILIZABLE; ese estado no se asigna por defecto.

`correo_electronicofe` se conserva con rol `por_definir` y estado REVISION hasta confirmar su significado. Un empleador no identificado o discrepante permanece almacenado para revision; no se reasigna a otra empresa por compartir cedula o dominio.

## Alcance de esta entrega

El script se ejecuta directamente en sqldb-jrb-sabana-dev con una identidad que pueda crear tablas, indices, vista y otorgar permisos. Requiere los modelos Excel, PST y terceros 09/10 existentes. Se prepara el permiso para `rol_carga_terceros`; no se crean usuarios ni se cambian identidades.

La validacion del modelo usa transacciones revertidas. El cargador asincrono ya esta preparado en `canopus/`; su configuracion y contrato estan en `CANOPUS_FUNCION.md`. Publicar Canopus actualiza directamente los candidatos de la vista. No se amplia la cola de avisos del maestro porque ese proceso aun no consume Canopus y excluye pacientes. La futura generacion de notificaciones pendientes tendra su propia integracion. El DDL 12 y el esquema historico 11 no se reescriben.

Pruebas SQL opt-in sobre objetos nuevos y con datos sinteticos:

```powershell
$env:CANOPUS_SQL_VALIDAR = '1'
python -m unittest discover -s tests -p test_canopus_modelo_sql.py -v
Remove-Item Env:CANOPUS_SQL_VALIDAR
```

El script es repetible, pero no altera definiciones de tablas ya existentes. Toda ampliacion posterior del modelo requiere una migracion nueva. El indice auxiliar en `ref.entidad_equivalencia` permite buscar por ID original del tercero para resolver su representante.
