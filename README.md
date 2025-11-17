# Conversor de PDF a Imágenes

Una herramienta de escritorio rápida y moderna para convertir archivos PDF a imágenes (WEBP, JPEG, PNG). Cuenta con una interfaz gráfica oscura (tema Cyborg), multiprocesamiento para mayor velocidad y opciones configurables de calidad y color.

## 📋 Requisitos Previos

### 1. Python
Asegúrate de tener Python instalado (versión 3.8 o superior recomendada).

### 2. Poppler (Motor de conversión)
Este proyecto requiere **Poppler** para procesar los PDFs. El script está configurado para buscarlo localmente.

1. Descarga la última versión de Poppler para Windows (busca "Release" con extension `.7z` o `.zip`).
2. Extrae el archivo descargado.
3. **Importante:** Copia **solo** la carpeta `bin` (que contiene `pdftoppm.exe`, etc.) dentro de una carpeta llamada `poppler` en la raíz de este proyecto.

La estructura de carpetas debe verse así:

```text
conversor de pdfs/
├── conversor_pdf.py                (Tu script)
├── requirements.txt
├── conversor_config.json  (Se crea automáticamente)
└── poppler/
    └── bin/
        ├── pdftoppm.exe
        ├── pdfinfo.exe
        └── ... (otros archivos dll y exe)

⚙️ Instalación y Ejecución

Sigue estos pasos para configurar tu entorno virtual e instalar las dependencias desde requirements.txt.

1. Crear y activar el entorno virtual

Abre una terminal en la carpeta del proyecto y ejecuta:

En Windows:
Bash

python -m venv venv
.\venv\Scripts\activate

(Verás que aparece (venv) al principio de tu línea de comandos).

2. Instalar dependencias

Con el entorno virtual activo, ejecuta:
Bash

pip install -r requirements.txt

3. Ejecutar la aplicación

Bash

python main.py

🛠️ Compilación (Opcional)

Si deseas convertir este script en un ejecutable (.exe), usa PyInstaller. Asegúrate de incluir la carpeta de Poppler:
Bash

pyinstaller --noconsole --onefile --add-data "poppler/bin;poppler/bin" main.py


### Resumen visual de lo que debes hacer:

1.  Crea el archivo **`requirements.txt`** con las 3 librerías.
2.  Crea la carpeta **`poppler`** y mete dentro la carpeta **`bin`** que descargaste de internet.
3.  Usa los comandos del README para instalar todo.
