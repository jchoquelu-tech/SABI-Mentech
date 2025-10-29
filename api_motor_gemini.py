# api_motor_gemini.py — v0.9
# Modelos separados TEXTO/JSON + Chat + Ítems + Microlección + Sugerencia adaptativa
# FIX: NLU amplía comandos: reintentar, repasar [tema], avanzar [tema].

import os, json, uuid, re, unicodedata
import google.generativeai as genai
from dotenv import load_dotenv

# ------------------ Init modelo ------------------
load_dotenv()
API_KEY = os.environ.get("GOOGLE_API_KEY")
MODEL_NAME = os.environ.get("GEMINI_MODEL", "models/gemini-pro-latest")
if not API_KEY:
    print("❌ Falta GOOGLE_API_KEY en .env")
genai.configure(api_key=API_KEY)

generation_config_json = genai.GenerationConfig(response_mime_type="application/json")
generation_config_text = genai.GenerationConfig(response_mime_type="text/plain")

try:
    json_model = genai.GenerativeModel(MODEL_NAME, generation_config=generation_config_json)
    text_model = genai.GenerativeModel(MODEL_NAME, generation_config=generation_config_text)
except Exception as e:
    print("❌ ERROR al inicializar Gemini:", e)
    json_model = None
    text_model = None

# ------------------ Utilidades ------------------
def _parse_json_lenient(texto):
    if not texto: return None
    try:
        return json.loads(texto)
    except Exception:
        m = re.search(r'\{.*\}', texto, re.S)
        if m:
            try: return json.loads(m.group(0))
            except Exception: return None
        return None

def _fallback_item(concepto_id, concepto_nombre, año, materia, dificultad=None):
    return {
        "item_id": str(uuid.uuid4()),
        "concepto_id": concepto_id,
        "pregunta": f"({materia} · {año}) Sobre «{concepto_nombre}»: ¿cuál afirmación es correcta?",
        "opciones": ["Afirmación 1", "Afirmación 2", "Afirmación 3", "Afirmación 4"],
        "respuesta_correcta": "Afirmación 1",
        **({"explicacion": "Revisa la definición clave y el ejemplo básico.",
            "dificultad": dificultad or "media"} if dificultad else {})
    }

def _norm(s: str) -> str:
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFD", s)
    return "".join(ch for ch in s if unicodedata.category(ch) != "Mn")

def _regex_nombre(mensaje: str):
    m = _norm(mensaje)
    r = re.search(r"(me llamo|mi nombre es|soy)\s+([a-záéíóúñ ]{2,40})", m)
    if r: return r.group(2).strip().title()
    return None

# ------------------ NLU ------------------
def interpretar_comando(mensaje: str):
    m = _norm(mensaje)
    nombre = _regex_nombre(mensaje)
    if nombre: return {"cmd":"set_nombre","nombre":nombre}

    # Dificultad y quiz length
    if "mas facil" in m or "más facil" in m or "facil" in m:   return {"cmd":"set_dificultad","nivel":"baja"}
    if "mas dificil" in m or "más dificil" in m or "dificil" in m: return {"cmd":"set_dificultad","nivel":"alta"}
    q = re.search(r"(quiz|prueba|examen)\s*(corto|largo)?\s*(de)?\s*(\d+)", m)
    if q: return {"cmd":"quiz_len","n":int(q.group(4))}

    # Decisiones principales tras el quiz (vía chat)
    if re.search(r"\b(reintentar|otra vez|intentar de nuevo|volver a intentar)\b", m):
        return {"cmd":"decision","accion":"reintentar"}

    repasar_match = re.search(r"\brepasar(?:\s+(fundamentos|prerrequisitos))?(?:\s+de\s+|[ :])?\s*([a-záéíóúñ0-9 ]{2,60})?$", m)
    if repasar_match:
        tema_txt = (requisar := repasar_match.group(2) or "").strip()
        return {"cmd":"decision","accion":"repasar","tema_text": tema_txt if tema_txt else None}

    avanzar_match = re.search(r"\b(avanzar|siguiente)\b(?:\s+(?:a|en))?\s*([a-záéíóúñ0-9 ]{2,60})?$", m)
    if avanzar_match:
        tema_txt = (avanzar_match.group(2) or "").strip()
        return {"cmd":"decision","accion":"avanzar","tema_text": tema_txt if tema_txt else None}

    # Otros comandos
    if "pista" in m:           return {"cmd":"pista"}
    if "explica" in m or "como se hace" in m or "¿como" in m or "cómo se hace" in m or "¿cómo" in m: return {"cmd":"explica"}
    if "ejemplo" in m:         return {"cmd":"ejemplo"}
    if "pausar" in m:          return {"cmd":"pausar"}
    if "retomar" in m or "continuar" in m: return {"cmd":"retomar"}
    if "resumen" in m or "donde me quede" in m or "dónde me quedé" in m: return {"cmd":"resumen"}
    if "cambiar de tema" in m or "cambiar tema" in m: return {"cmd":"cambiar_tema"}
    return {"cmd":None}

def interpretar_intencion_usuario(mensaje: str, mundos_disponibles: set, grados_disponibles: set):
    def heuristica(m):
        m0 = _norm(m)
        objetivo = None
        if any(k in m0 for k in ["repasar","revisar","reforzar"]): objetivo = "repasar"
        elif any(k in m0 for k in ["explorar","aprender","ver"]):   objetivo = "explorar"
        elif any(k in m0 for k in ["prepararme","admision","preu","pre-u","universidad","simulacro"]): objetivo = "pre_u"
        mundo = None
        for md in mundos_disponibles:
            if _norm(md) in m0: mundo = md; break
        grado = None
        m_num = re.search(r"\b(1|2|3|4|5)\s*(ro|to|do)?\b", m0)
        if m_num:
            mapa={"1":"1ro de secundaria","2":"2do de secundaria","3":"3ro de secundaria","4":"4to de secundaria","5":"5to de secundaria"}
            g = mapa.get(m_num.group(1))
            if g in grados_disponibles: grado = g
        if grado is None and "secundaria" in m0 and objetivo=="pre_u" and "5to de secundaria" in grados_disponibles:
            grado = "5to de secundaria"
        return {"objetivo":objetivo,"mundo":mundo,"grado":grado,"confianza":0.5}

    if json_model is None:
        return heuristica(mensaje)

    prompt = f"""
Eres un parser NLU. Lee el MENSAJE y devuelve SOLO JSON:
{{
 "objetivo": "repasar | explorar | pre_u",
 "mundo": "uno EXACTO de {list(mundos_disponibles)}",
 "grado": "uno EXACTO de {list(grados_disponibles)} o null",
 "confianza": 0.0..1.0
}}
MENSAJE:
\"\"\"{mensaje}\"\"\""""
    try:
        r = json_model.generate_content(prompt)
        data = _parse_json_lenient(getattr(r, "text", ""))
        if not data: raise ValueError("JSON vacío/no válido")
        obj, mun, gra = data.get("objetivo"), data.get("mundo"), data.get("grado")
        conf = float(data.get("confianza", 0.7))
        if obj not in ["repasar","explorar","pre_u"]: obj = None
        if mun not in mundos_disponibles: mun = None
        if gra is not None and gra not in grados_disponibles: gra = None
        if not (obj and mun):
            base = heuristica(mensaje)
            obj = obj or base["objetivo"]; mun = mun or base["mundo"]; gra = gra or base["grado"]; conf = min(conf,0.75)
        return {"objetivo":obj,"mundo":mun,"grado":gra,"confianza":conf}
    except Exception as e:
        print("❌ Error NLU:", e)
        return heuristica(mensaje)

# ------------------ Chat pedagógico ------------------
def sabi_chat(mensaje: str, contexto: dict=None) -> str:
    if text_model is None:
        return "No hay modelo Gemini configurado. Revisa GOOGLE_API_KEY."
    sistema = (
        "Eres Sabi, un tutor empático. Responde SIEMPRE en español y en Markdown; nunca devuelvas JSON. "
        "Usa pasos claros, viñetas y tono motivador. Si te piden PISTA 1 o 2, da 2-3 frases y 2 bullets. Evita respuestas largas."
    )
    contenido = f"Contexto: {json.dumps(contexto or {}, ensure_ascii=False)}\nUsuario: {mensaje}"
    try:
        r = text_model.generate_content([sistema, contenido])
        return (getattr(r, "text", "") or "").strip()
    except Exception as e:
        return f"(Sabi) Hubo un error: {e}"

# ------------------ Micro-lección ------------------
def generar_micro_leccion(concepto: str, nivel: str, materia: str) -> dict:
    if json_model is None: return None
    prompt = f"""
Genera una micro-lección para:
- Concepto: {concepto}
- Materia: {materia}
- Nivel: {nivel}

Devuelve SOLO JSON:
{{
 "definicion": "... (1-2 oraciones)",
 "pasos": ["paso 1","paso 2","paso 3"],
 "ejemplo": "ejemplo resuelto breve",
 "practica_rapida": [
   {{"pregunta":"...","respuesta":"..."}},
   {{"pregunta":"...","respuesta":"..."}}
 ]
}}
"""
    r = None
    try:
        r = json_model.generate_content(prompt)
        return _parse_json_lenient(getattr(r, "text", "")) or None
    except Exception as e:
        print("❌ Error micro-lección:", e, getattr(r, "text", ""))
        return None

# ------------------ Ítems ------------------
def generar_item_para_concepto(concepto_id: str, concepto_nombre: str, año: str, materia: str) -> dict:
    if json_model is None:
        print("❌ Modelo JSON no disponible.")
        return _fallback_item(concepto_id, concepto_nombre, año, materia)

    prompt = f"""
Genera UN ítem de opción múltiple (4 opciones, 1 correcta) para:
- Concepto: "{concepto_nombre}" | Materia: "{materia}" | Nivel: "{año}"

SOLO JSON:
{{"pregunta":"...","opciones":["A","B","C","D"],"respuesta_correcta":"..."}}
"""
    r = None
    try:
        r = json_model.generate_content(prompt)
        data = _parse_json_lenient(getattr(r, "text", ""))
        if not data:
            raise ValueError("Salida del modelo no es JSON válido.")
        if not isinstance(data.get("opciones"), list) or len(data["opciones"]) != 4:
            raise ValueError("Debe traer 4 opciones.")
        if data.get("respuesta_correcta") not in data["opciones"]:
            raise ValueError("La respuesta debe estar en las opciones.")
        data["item_id"] = str(uuid.uuid4())
        data["concepto_id"] = concepto_id
        return data
    except Exception as e:
        msg = getattr(r, "text", "sin respuesta del modelo")
        print(f"❌ Error generar_item_para_concepto: {e}\n↳ Respuesta: {msg}")
        return _fallback_item(concepto_id, concepto_nombre, año, materia)

def generar_item_con_explicacion(concepto_id: str, concepto_nombre: str, año: str, materia: str, dificultad: str="media") -> dict:
    if json_model is None:
        print("❌ Modelo JSON no disponible.")
        return _fallback_item(concepto_id, concepto_nombre, año, materia, dificultad=dificultad)

    prompt = f"""
Genera 1 pregunta de opción múltiple **{dificultad}** para:
Concepto: "{concepto_nombre}" | Materia: "{materia}" | Nivel: "{año}"

SOLO JSON:
{{
 "pregunta":"...",
 "opciones":["A","B","C","D"],
 "respuesta_correcta":"...",
 "explicacion":"2-4 pasos claros",
 "dificultad":"{dificultad}"
}}
"""
    r = None
    try:
        r = json_model.generate_content(prompt)
        data = _parse_json_lenient(getattr(r, "text", ""))
        if not data:
            raise ValueError("Salida del modelo no es JSON válido.")
        if not isinstance(data.get("opciones"), list) or len(data["opciones"]) != 4:
            raise ValueError("Debe traer 4 opciones.")
        if data.get("respuesta_correcta") not in data["opciones"]:
            raise ValueError("La respuesta debe estar en las opciones.")
        data["item_id"] = str(uuid.uuid4())
        data["concepto_id"] = concepto_id
        data["dificultad"] = dificultad
        return data
    except Exception as e:
        msg = getattr(r, "text", "sin respuesta del modelo")
        print(f"❌ Error generar_item_con_explicacion: {e}\n↳ Respuesta: {msg}")
        return _fallback_item(concepto_id, concepto_nombre, año, materia, dificultad=dificultad)

# ------------------ Sugerencia Adaptativa ------------------
PROMPT_SUGERENCIA = """
Eres Sabi, un tutor de aprendizaje adaptativo.
Principios: prerrequisitos si hay atasco; avanzar si hay dominio; explorar si hay interés; explica la conexión; mapa {dominados, en_practica, siguiente}; metacognición; tono motivador.
Devuelve SOLO JSON (español):
{
  "decision": "repasar_prerrequisitos | reintentar | avanzar | explorar_conectados",
  "siguiente_concepto": {"id":"...","nombre":"...","dificultad_sugerida":"baja|media|alta","razon":"..."},
  "alternativas": [{"id":"...","nombre":"...","tipo":"prerrequisito|avance|exploracion","razon":"..."}],
  "explicacion_relacion":"...",
  "mapa_ruta":{"dominados":[{"id":"...","nombre":"..."}],"en_practica":[{"id":"...","nombre":"..."}],"siguiente":[{"id":"...","nombre":"..."}]},
  "metacognicion":"...",
  "mensaje_motivacional":"...",
  "ajuste_dificultad":"baja|igual|sube_1|sube_2",
  "confianza": 0.0
}
"""

def sugerir_siguiente_concepto(estado_estudiante: dict) -> dict:
    if json_model is None:
        return {
            "decision":"reintentar",
            "siguiente_concepto": {
                "id": estado_estudiante.get("concepto_actual",{}).get("id",""),
                "nombre": estado_estudiante.get("concepto_actual",{}).get("nombre",""),
                "dificultad_sugerida":"media",
                "razon":"Volvamos a intentarlo con variación leve."
            },
            "alternativas": [],
            "explicacion_relacion":"Retomamos lo recién trabajado para consolidar.",
            "mapa_ruta":{"dominados":[],"en_practica":[estado_estudiante.get("concepto_actual",{})],"siguiente":[]},
            "metacognicion":"¿Prefieres una pista o un ejemplo antes de intentar de nuevo?",
            "mensaje_motivacional":"Equivocarse es parte del aprendizaje, ¡vamos paso a paso!",
            "ajuste_dificultad":"igual",
            "confianza":0.3
        }
    r = None
    try:
        r = json_model.generate_content([PROMPT_SUGERENCIA, json.dumps(estado_estudiante, ensure_ascii=False)])
        data = _parse_json_lenient(getattr(r, "text", ""))
        return data or None
    except Exception as e:
        print("❌ Error en sugerir_siguiente_concepto:", e, getattr(r, "text", ""))
        return None
