# Procesamiento de notificaciones JRB

Repositorio de código para consolidar reportes de SharePoint en Azure SQL, tabularizar PST, desarrollar cruces de envío y lectura y procesar insumos de auditoría. Los archivos originales y los resultados de las auditorías se conservan fuera de Git.

La raíz de este repositorio es la carpeta `Junta Regional Bogotá - Notificaciones JB` y su remoto es `https://github.com/efzp/notificaciones_jrb`. `03_trabajo/function_sabana` es una subcarpeta del mismo repositorio: los commits realizados desde la raíz incluyen sus cambios cuando se agregan a la preparación.

## Componentes

| Ruta | Contenido |
|---|---|
| `03_trabajo/function_sabana/` | Azure Functions para Excel y PST, pruebas y scripts SQL de la aplicación |
| `03_trabajo/modelo_datos/` | Diseño inicial del modelo SQL y herramientas de validación |
| `03_trabajo/analisis_estructura_sabana/` | Scripts de inspección de los reportes y del modelo de referencia |
| `03_trabajo/actualizacion_sabana/` | Herramientas de actualización y verificación de cargas |
| `03_trabajo/validacion_function_azure/` | Clientes para validar la aplicación desplegada |
| `03_trabajo/piloto_20/` y `03_trabajo/piloto_100/` | Herramientas de exploración local de PST |
| `.github/workflows/deploy-function.yml` | Pruebas y despliegue de la Function App |

La configuración de la aplicación está en [la documentación de la Function](03_trabajo/function_sabana/README.md). Para PST, consultar [SQL, identidades y Power Automate](03_trabajo/function_sabana/PST_CONFIGURACION.md).

## Pruebas y despliegue

Desde la raíz, con Python 3.12 y las dependencias instaladas:

```powershell
Set-Location .\03_trabajo\function_sabana
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
```

GitHub Actions prueba los cambios de la Function y publica desde `main` después de superar las pruebas. El workflow se ejecuta sobre `03_trabajo/function_sabana` y crea un paquete que contiene únicamente los archivos de ejecución de esa aplicación. Los demás scripts no se publican en Azure automáticamente.

Se conserva el repositorio de GitHub, su rama `main`, sus secretos y variables y la autenticación OIDC existentes. Las rutas de Power Automate y el nombre de la Function App no cambian por esta reorganización del código.

## Trabajo local y archivos excluidos

Abrir esta carpeta raíz en VS Code para trabajar con un solo repositorio. La carpeta de la Function ya no tiene un repositorio Git independiente.

Los PST, Excel, correos, fuentes, evidencia, resultados de procesamiento, dependencias, credenciales locales y respaldos de la migración se excluyen mediante `.gitignore`. Los scripts de extracción o carga pueden leer esos archivos locales, pero sus datos no se incorporan a Git ni al paquete de despliegue.

Las carpetas de alcance, hallazgos e informe pertenecen al espacio local de auditoría y sus documentos no se incluyen automáticamente en los commits de código.
