from pyBKT.models import Model
import numpy as np

model = Model(seed=42)

DEFAULTS = {
    'order_id': 'concepto_id',
    'skill_name': 'concepto_id',
    'correct': 'correcta',
    'user_id': 'id_usuario',
    'multigs': 'id_usuario',
    'forgets': False,          
    'slip': 0.15,              
    'guess': 0.25,             
    'transit': 0.10,           
    'prior': 0.25              
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
        
        return DEFAULTS['prior']

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

    try:
        model.fit(data=data, defaults=DEFAULTS, multigs=True)
    
        preds = model.predict(data=data, multigs=True)
        
        siguiente_prob_maestria = preds.iloc[-1]['correct_predictions_multigs']
        
        return float(siguiente_prob_maestria)

    except Exception as e:
        print(f"Error en BKT: {e}. Datos: {data}")
        return DEFAULTS['prior']

if __name__ == "__main__":
    print("Probando el motor BKT...")

    user = "estudiante_prueba"
    concept = "1_ARIT_01"

    historial = []
    prob_1 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Probabilidad inicial: {prob_1:.2f}") 

    historial.append((0, 1)) 
    prob_2 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Después de 1 fallo: {prob_2:.2f}") 

    historial.append((1, 2)) 
    prob_3 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Después de 1 acierto: {prob_3:.2f}") 

    historial.append((1, 3))
    prob_4 = obtener_nueva_probabilidad(user, concept, historial)
    print(f"Después de 2 aciertos: {prob_4:.2f}") 
