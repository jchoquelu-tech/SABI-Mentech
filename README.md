# Sabi — Tutor Inteligente

Interfaz tipo WhatsApp construida en **Pygame** para practicar Matemática con un tutor IA (“Sabi”):
- Chat pedagógico con **Gemini** (o modo **offline** con fallbacks).
- Misiones por **objetivo**, **mundo (materia)** y **grado**.
- Preguntas adaptativas con **BKT** (Bayesian Knowledge Tracing) y reglas locales.
- **Intro en video** (8 s) antes de iniciar + **mascota/avatares** circulares.
- Chat **minimizable** y con **scroll** suave.

> También se incluye una versión web (experimental) en `app.py` con **Streamlit**.

---

## 🗂️ Estructura del proyecto
```text
ProyectoSABI/
├─ assets/
│ ├─ sabi.png # Mascota (recomendado PNG con fondo transparente)
│ └─ intro.mp4 # Video de introducción (8 s recomendado)
├─ api_motor_gemini.py # Motor IA (interpreta comando, chat, generación de ítems)
├─ logica_bkt.py # Lógica BKT (Bayesian Knowledge Tracing)
├─ setup_database.py # Creación/migración de BD (SQLite)
├─ grafo_conocimiento_NODOS.json # Nodos del grafo (temas)
├─ grafo_conocimiento_ARISTAS.json # Aristas (prerrequisitos)
├─ banco_items.json # Banco local de preguntas (cache/seed)
├─ main_pygame.py # Aplicación Pygame (UI + lógica híbrida)
├─ app.py # Aplicación Streamlit (opcional)
├─ requirements.txt
├─ .env.example
└─ README.md
```
---

## ⚙️ Requisitos

- **Python 3.10+** (se probó en 3.11).
- Windows, Linux o macOS.
- *(Opcional)* **OpenCV** para reproducir el video de intro. Si no está, se usa un **fallback animado**.

---

## 🚀 Instalación (Windows / PowerShell)

```powershell
git clone https://github.com/<tu-usuario>/<tu-repo>.git
cd <tu-repo>

python -m venv .venv
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
copy .env.example .env
Edita .env y coloca tu clave de Gemini:

GEMINI_API_KEY=tu_clave_aqui


Si no tienes clave o prefieres no usarla, la app corre en modo offline con generación de preguntas y sugerencias locales.
▶️ Ejecución
Modo Pygame (recomendado)
python main_pygame.py

Modo Streamlit (opcional, versión web)
streamlit run app.py

🧩 Datos y formatos
grafo_conocimiento_NODOS.json

Lista de conceptos:

[
  { "id": "A1", "concepto": "Polinomios", "materia": "Álgebra", "año": "4to de secundaria" },
  { "id": "A2", "concepto": "Factorización", "materia": "Álgebra", "año": "4to de secundaria" }
]

grafo_conocimiento_ARISTAS.json

Lista de prerrequisitos:

[
  { "de": "A1", "a": "A2" }  // A1 es prerequisito de A2
]

banco_items.json

Banco de preguntas cacheadas/generadas:

[
  {
    "item_id": "A1-q1",
    "concepto_id": "A1",
    "pregunta": "¿Cuál es ...?",
    "opciones": ["...","...","...","..."],
    "respuesta_correcta": "...",
    "dificultad": "media"
  }
]

🎞️ Assets (mascota y video)

Mascota: coloca assets/sabi.png (cuadrado, ≥256x256, fondo transparente). El sistema lo recorta en círculo automáticamente.

Video de intro: coloca assets/intro.mp4 (o .webm/.mov/.avi/.mkv). Máximo recomendado: 8 s.

Si tu archivo se llama introsabi.mp4, renómbralo a intro.mp4.

Sin OpenCV instalado → se muestra una animación fallback con la mascota.

🧠 Modo Online vs Offline

Online: si api_motor_gemini.py encuentra GEMINI_API_KEY, se usan:

Interpretación de intención,

Chat pedagógico,

Generación de preguntas y micro‑lecciones,

Sugerencias de siguiente tema.

Offline: si no hay API, el sistema:

Usa preguntas locales/fallback,

Sugerencias por reglas + BKT básico.

🕹️ Uso rápido

Intro: se reproduce el video (ESC/SPACE para saltarlo).

Login: escribe tu ID de estudiante y presiona Enter.

Menú: elige Objetivo, Mundo (materia) y Grado → “¡Comenzar Aventura!”.

Mapa: selecciona un tema (o usa el chat para pedirlo con texto).

Práctica:

Se generan preguntas tipo test.

El progreso se registra en SQLite (usuarios.db).

Chat:

Clic en el input → escribe y Enter.

Scroll con la rueda dentro del panel.

Botón minimizar en la cabecera.

Comandos útiles:

Explorar/Repasar/Pre_U + [mundo] + [año]

tema: polinomios (o “Quiero repasar funciones”)

dificultad: fácil|media|difícil

quiz: 5 (cambia la longitud del quiz)

pausar, retomar, resumen, cambiar tema

reintentar, repasar [tema], avanzar [tema] (después de un quiz)

🗄️ Base de datos (SQLite)

setup_database.py crea/migra el esquema:

usuarios: registro básico.

dominio_usuario: probabilidad de dominio por concepto.

sesiones: objetivo/mundo/grado/tema y tiempos.

historial_respuestas: cada respuesta (tiempo, pistas, dificultad).

La app escribe en usuarios.db en el directorio del proyecto.

🧪 Consejos y solución de problemas

Pantalla del chat en blanco / burbujas tapadas
Asegúrate de blitear el chat_surface después de dibujar el fondo, y no redibujar luego un rectángulo blanco encima. En main_pygame.py ya está corregido.

NameError: panel is not defined
Define panel = pygame.Rect(... ) al inicio de render_chat_panel antes de usarlo.

No se ve la mascota (SVG)
Pygame no soporta SVG nativo. Usa assets/sabi.png. (El código puede convertir SVG con cairosvg si está instalado, pero PNG es más robusto).

El video no se reproduce

Renombra a assets/intro.mp4.

Instala OpenCV: pip install opencv-python numpy.

Si falla: se usa la animación fallback automáticamente.

Caracteres raros/emoji en el chat (“□”)
Ya se filtran en clean_text_for_chat. Evita emojis y caracteres de control.

banco_items.json vacío
La primera ejecución puede tardar un poco si ONLINE_MODE está activo y genera ítems. En offline, se usan fallbacks.

🧰 Desarrollo

Entorno virtual

python -m venv .venv
# Windows:
.venv\Scripts\Activate.ps1
# Linux/macOS:
source .venv/bin/activate


Formateo (opcional)

pip install ruff black
ruff check .
black .


Empaquetar .exe (Windows, opcional)

pip install pyinstaller
pyinstaller --noconfirm --onefile --name SabiTutor main_pygame.py ^
  --add-data "assets;assets" ^
  --hidden-import pygame

