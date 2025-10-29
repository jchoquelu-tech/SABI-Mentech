from pyBKT.models import Model
import numpy as np

# Inicializamos un modelo BKT estándar.
# Lo entrenaremos "en vivo" (online) con los datos del estudiante.
model = Model(seed=42)

# --- Parámetros Estándar de BKT ---
# Estos son valores iniciales razonables para un hackathon.
# No necesitas entenderlos a fondo, solo saber que existen.
DEFAULTS = {
    'order_id': 'concepto_id',
    'skill_name': 'concepto_id',
    'correct': 'correcta',
    'user_id': 'id_usuario',
    'multigs': 'id_usuario',
    'forgets': False,          # No modelaremos el olvido (más simple)
    'slip': 0.15,              # Prob. de fallar sabiendo (P(S))
    'guess': 0.25,             # Prob. de adivinar sin saber (P(G))
    'transit': 0.10,           # Prob. de aprender (P(T))
    'prior': 0.25              # Prob. inicial de saber (P(L0))
}

def obtener_nueva_probabilidad(id_usuario, concepto_id, historial_respuestas):
    """
    Calcula la *siguiente* probabilidad de maestría para un concepto,
    basado en todas las respuestas anteriores de un usuario.
    
    :param id_usuario: (string) ID del usuario
    :param concepto_id: (string) ID del concepto (ej. '1_ARIT_01')
    :param historial_respuestas: (list of tuples) Lista de (respuesta_correcta, timestamp)
                                  ej. [(1, 1), (0, 2), (1, 3)]
                                  1 = correcta, 0 = incorrecta
    :return: (float) La nueva probabilidad de maestría
    """
    
    if not historial_respuestas:
        # Si no hay historial, devolvemos la prob. inicial
        return DEFAULTS['prior']

    # 1. Formatear los datos para pyBKT
    # Necesita un formato específico de "data frame"
    data = {
        'order_id': [],
        'skill_name': [],
        'correct': [],
        'user_id': []
    }
    
    for i, (correcta, _) in enumerate(historial_respuestas):
        data['order_id'].append(i + 1)
        data['skill_name'].append(concepto_id)
        data['correct'].append(correcta)
        data['user_id'].append(id_usuario)

    # 2. "Entrenar" el modelo BKT con los datos del historial
    #    (Esto es súper rápido, solo ajusta los parámetros)
    try:
        model.fit(data=data, defaults=DEFAULTS, multigs=True)
    
        # 3. Predecir la probabilidad de la *siguiente* respuesta
        #    Esta es la "probabilidad de maestría" actual
        preds = model.predict(data=data, multigs=True)
        
        # La predicción que nos interesa es la última
        siguiente_prob_maestria = preds.iloc[-1]['correct_predictions_multigs']
        
        return float(siguiente_prob_maestria)

    except Exception as e:
        print(f"Error en BKT: {e}. Datos: {data}")
        # Si falla, devuelve la última probabilidad conocida o el prior
        return DEFAULTS['prior']


# --- BLOQUE DE PRUEBA ---
if __name__ == "__main__":
    print("Probando el motor BKT...")
    
    # Simulación de un estudiante
    user = "estudiante_prueba"
    concept = "1_ARIT_01"
    
    # 1. Sin historial
    historial = []
    prob_1 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Probabilidad inicial: {prob_1:.2f}") # Debería ser 0.25
    
    # 2. Falla la primera
    historial.append((0, 1)) # (incorrecta, timestamp 1)
    prob_2 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Después de 1 fallo: {prob_2:.2f}") # Debería ser más baja
    
    # 3. Acierta la segunda
    historial.append((1, 2)) # (correcta, timestamp 2)
    prob_3 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Después de 1 acierto: {prob_3:.2f}") # Debería subir
    
    # 4. Acierta la tercera
    historial.append((1, 3)) # (correcta, timestamp 3)
    prob_4 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Después de 2 aciertos: {prob_4:.2f}") # Debería subir más