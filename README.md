# Zellij Whisper Talk

Este plugin de Zellij te permite grabar notas de voz desde el micrófono, transcribirlas de manera ultra rápida con la API de OpenRouter (Whisper) y limpiar la transcripción usando un modelo de LLM (por defecto deepseek/deepseek-v4-flash) antes de inyectar el texto resultante directamente en la terminal activa en donde esté tu cursor.

## Arquitectura Híbrida

Debido a que el sandbox de WebAssembly (WASI) de Zellij no tiene acceso al hardware (micrófono), el plugin utiliza una arquitectura híbrida:
1. **Plugin (WASM):** Controla el estado en pantalla (Grabando, Transcribiendo, etc.), gestiona los eventos de teclado (Espacio/Enter/Esc) y controla el script host nativo a través de un archivo lock de control en `/tmp`.
2. **Host Script (Python 3):** Corre en el host con **cero dependencias externas** (usa la librería estándar). Llama a `arecord` para capturar audio de 16kHz mono WAV, codifica a base64, realiza la transcripción y ejecuta el prompt de limpieza usando la API de OpenRouter.

## Requisitos del Sistema

* **Linux** con `arecord` instalado (parte del paquete `alsa-utils`, preinstalado en la mayoría de distros).
* **Python 3** (instalado por defecto en la gran mayoría de sistemas Linux).
* **OpenRouter API Key** configurada en tu entorno.

---

## Cómo Probarlo Rápido (Modo Layout)

1. Asegúrate de exportar tu API Key en tu sesión de terminal:
   ```bash
   export OPENROUTER_API_KEY="tu-api-key-de-openrouter"
   ```

2. Ejecuta Zellij cargando la configuración de prueba:
   ```bash
   zellij --layout /mnt/E608E9D408E9A431/Caprinosol/zellij-voice-input/plugin.kdl
   ```

3. Presiona **`Espacio`** para empezar a grabar, habla, y presiona **`Espacio`** de nuevo para detener la grabación. ¡El texto limpio se insertará en tu cursor de terminal activo!

---

## Integración Permanente en tu Configuración de Zellij (`config.kdl`)

Para activar el plugin en cualquier momento con un atajo de teclado global (por ejemplo, `Ctrl + y`), edita tu archivo de configuración de Zellij (normalmente en `~/.config/zellij/config.kdl`):

```kdl
keybinds {
    shared {
        bind "Ctrl y" {
            LaunchOrFocusPlugin "file:/mnt/E608E9D408E9A431/Caprinosol/zellij-voice-input/target/wasm32-wasip1/release/zellij-whisper-talk.wasm" {
                floating true
                script_path "/mnt/E608E9D408E9A431/Caprinosol/zellij-voice-input/scripts/transcribe.py"
                model "deepseek/deepseek-v4-flash" // Modelo para limpiar el audio
            }
        }
    }
}
```

## Personalización

Puedes modificar el script `/mnt/E608E9D408E9A431/Caprinosol/zellij-voice-input/scripts/transcribe.py` para:
* Cambiar el **prompt de limpieza de audio** (`CLEANUP_SYSTEM_PROMPT`) que elimina muletillas y evita la inyección de instrucciones no deseadas.
* Cambiar el modelo de transcripción por defecto.
