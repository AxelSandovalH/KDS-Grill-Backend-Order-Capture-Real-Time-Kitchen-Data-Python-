# app.py (tu backend Flask-SocketIO)
from flask import Flask, render_template, request # <--- ¡Asegúrate de importar 'request'!
from flask_socketio import SocketIO, emit
import base64
import cv2 # Para la webcam
import time
import os
import threading
from datetime import datetime

app = Flask(__name__)
# Configuración básica de SocketIO - para producción, usar un broker de mensajes como Redis
app.config['SECRET_KEY'] = 'tu_clave_secreta_aqui_CAMBIAME' # ¡CAMBIA ESTO EN PRODUCCIÓN!
socketio = SocketIO(app, cors_allowed_origins="*") # Permite conexión desde cualquier origen (cambiar para producción)

# --- Simulación de la Captura de Imagen y Procesamiento ---
# NOTA: En un caso real, esto estaría disparado por un botón físico, un sensor, etc.
# Por ahora, lo simularemos con una función o hilo.

def capture_and_send_order():
    """Simula la captura de una comanda y la envía via WebSocket."""
    global order_counter # Usaremos un contador global simple para IDs

    # 1. Simular Captura de Imagen con Webcam (Usando OpenCV)
    # Descomenta y ajusta esta sección cuando conectes la webcam real:
    # cap = cv2.VideoCapture(0) # 0 es el ID de la webcam, puede variar (prueba 1, 2, etc.)
    # if not cap.isOpened():
    #     print("Error: No se pudo acceder a la webcam. Asegúrate de que esté conectada y los drivers estén instalados.")
    #     # Intentar con un ID diferente o salir
    #     return

    # # Configurar resolución (opcional, pero recomendado para 4K)
    # # cap.set(cv2.CAP_PROP_FRAME_WIDTH, 3840) # Ancho 4K
    # # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 2160) # Alto 4K

    # ret, frame = cap.read()
    # cap.release() # Importante liberar la cámara

    # if not ret:
    #     print("Error: No se pudo capturar el frame de la webcam.")
    #     return

    # # Codificar el frame capturado a Base64
    # _, buffer = cv2.imencode('.png', frame) # O '.jpg' para menor tamaño
    # encoded_image_string = base64.b64encode(buffer).decode('utf-8')


    # --- PARA PRUEBAS SIN WEBCAM REAL: USAR UNA IMAGEN DE MUESTRA ---
    # Asegúrate de tener un archivo 'sample_comanda.png' en la misma carpeta del script.
    try:
        with open("sample_comanda.png", "rb") as image_file:
            encoded_image_string = base64.b64encode(image_file.read()).decode('utf-8')
    except FileNotFoundError:
        print("ERROR: sample_comanda.png no encontrada. Crea una para probar el flujo de imagen.")
        # Si no hay imagen, puedes enviar una cadena vacía o una imagen de placeholder pequeña en base64
        encoded_image_string = ""

    order_counter += 1 # Incrementa el contador de órdenes

    # 2. Simular Extracción de Datos de la Comanda (Manual o con OCR futuro)
    # Por ahora, datos mock. En el futuro aquí integrarías tu lógica de OCR o procesamiento.
    new_order_data = {
        'id': f'KDS-{order_counter:03d}',
        'table': (order_counter % 11) + 1, # Mesas del 1 al 10
        'startedAt': datetime.now().strftime('%H:%M'), # Hora de inicio actual
        'status': 'NEW', # O 'COOKING' si ya entra directamente a ese estado
        'initialDuration': 15 * 60, # 15 minutos en segundos (valor por defecto)
        'timeRemaining': '15:00', # Valor inicial que React recalculará
        'image': f'data:image/png;base64,{encoded_image_string}' # Imagen en Base64
        # Si optas por guardar la imagen en el servidor y enviar URL (más eficiente para 4K):
        # 'image_url': f'/static/images/{img_filename}' (requiere servir archivos estáticos en Flask)
    }

    print(f"Emitiendo nueva orden: {new_order_data['id']}")
    # 3. Emitir la Nueva Orden a todos los clientes conectados via WebSocket
    socketio.emit('new_order', new_order_data)

# Variable global para simular el contador de órdenes
order_counter = 0

# --- Rutas HTTP Básicas (opcional, para React inicial) ---
@app.route('/')
def index():
    return "KDS Grill Backend - WebSockets Active"

# Endpoint para simular una nueva orden manualmente (para pruebas)
@app.route('/add_mock_order')
def add_mock_order():
    # Necesario para que capture_and_send_order pueda emitir via socketio
    with app.app_context():
        capture_and_send_order()
    return "Mock order added via WebSocket!"

# --- Eventos de WebSocket ---
@socketio.on('connect')
def test_connect(auth=None): # <-- ¡ESTE ES EL CAMBIO CLAVE! Aceptar 'auth'
    print('Cliente conectado:', request.sid)
    # Opcional: Cuando un nuevo cliente se conecta, puedes enviarle el estado actual de todas las órdenes
    # Esto requeriría que tengas un estado global de órdenes en tu backend, o las consultes de una DB.
    # from flask_socketio import join_room # Si usas rooms
    # join_room(request.sid) # Unir al cliente a su propia 'sala' si lo necesitas para mensajes directos
    # emit('current_orders', list_of_all_current_orders_from_db) # Si tuvieras una DB

@socketio.on('disconnect')
def test_disconnect():
    print('Cliente desconectado:', request.sid)

@socketio.on('update_order_status')
def handle_update_order_status(data):
    # Aquí recibirías la actualización de React cuando un chef marca una orden como READY o ALMOST_DONE
    order_id = data.get('order_id')
    new_status = data.get('status')
    print(f"Recibida actualización de orden {order_id} a estado {new_status} desde el frontend.")
    # En un sistema real, aquí:
    # 1. Validarías la data.
    # 2. Actualizarías el estado de esta orden en tu BASE DE DATOS.
    # 3. Si la actualización es relevante para otros clientes (ej. otra pantalla KDS o una app de expedición),
    #    la emitirías de vuelta:
    #    socketio.emit('order_updated', {'id': order_id, 'status': new_status, 'updatedBy': 'chef'})

# --- Hilo para simular órdenes automáticas (para pruebas) ---
def auto_order_generator():
    while True:
        # Usa el tiempo actual para que la generación sea más acorde al horario de Manzanillo
        # Current time is Wednesday, July 30, 2025 at 7:02:08 PM CST.
        # Simula una nueva orden cada 10 segundos
        time.sleep(10)
        with app.app_context(): # Necesario para emitir desde fuera de una petición
            capture_and_send_order()

# Iniciar el generador de órdenes automáticas en un hilo separado
# Esto hará que las órdenes empiecen a aparecer en tu KDS automáticamente.
order_thread = threading.Thread(target=auto_order_generator)
order_thread.daemon = True # Permite que el hilo termine cuando la app principal lo hace
order_thread.start()


if __name__ == '__main__':
    print("Iniciando Flask-SocketIO server...")
    # Asegúrate de que Flask-SocketIO use gevent o eventlet para producción.
    # Si tienes problemas, primero prueba sin estas librerías, solo con flask-socketio básico.
    # Si vas a usar gevent, instala: pip install gevent gevent-websocket
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)