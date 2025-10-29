from dotenv import load_dotenv
import os
import google.generativeai as genai

load_dotenv()
api_key = os.environ.get('GOOGLE_API_KEY')
MODEL_NAME = os.environ.get('GEMINI_MODEL', 'models/gemini-pro')
genai.configure(api_key=api_key)

# (opcional, para que veas los modelos disponibles)
print("Modelos disponibles en tu cuenta:")
for m in genai.list_models():
    print(m.name)

generation_config = genai.GenerationConfig(
    response_mime_type="application/json"
)
try:
    model = genai.GenerativeModel(
        MODEL_NAME,
        generation_config=generation_config
    )
    print(f"🤖 Modelo Gemini ('{MODEL_NAME}' con salida JSON) inicializado.")
except Exception as e:
    print(f"❌ ¡ERROR AL INICIALIZAR EL MODELO GEMINI!: {e}")
    model = None
