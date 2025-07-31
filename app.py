# app.py
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import base64
import cv2 # Para la webcam
import time
import os
import threading
from datetime import datetime

app = Flask(__name__)
app.config['SECRET_KEY'] = 'tu_clave_secreta_aqui_CAMBIAME'
socketio = SocketIO(app, cors_allowed_origins="*")

# Variable global para simular el contador de órdenes
order_counter = 0

# --- Función para capturar y enviar la orden (AHORA CON WEBCAM Y VISTA PREVIA) ---
def capture_and_send_order():
    """Captura una imagen de la webcam, la muestra, la codifica y envía via WebSocket."""
    global order_counter

    encoded_image_string = "" # Inicializar por si la captura falla
    frame_to_show = None # Para almacenar el frame a mostrar
    
    # Intenta acceder a la cámara con ID 0, luego ID 1 si el 0 falla
    cap_id = 0
    cap = cv2.VideoCapture(cap_id)
    if not cap.isOpened():
        print(f"ERROR: No se pudo acceder a la webcam con ID {cap_id}. Intentando con ID 1...")
        cap_id = 1
        cap = cv2.VideoCapture(cap_id)
        if not cap.isOpened():
            print(f"ERROR: Tampoco se pudo acceder a la webcam con ID {cap_id}. Verifica las conexiones o ID.")
            # Si no hay cámara, intentamos cargar una imagen de fallback
            try:
                with open("sample_comanda_fallback.png", "rb") as image_file:
                    encoded_image_string = base64.b64encode(image_file.read()).decode('utf-8')
                print("Usando imagen de fallback debido a la falta de webcam.")
            except FileNotFoundError:
                print("No hay webcam y tampoco imagen de fallback 'sample_comanda_fallback.png'. La orden se enviará sin imagen.")
            
            # Si hay una imagen de fallback, la cargamos para mostrarla
            if os.path.exists("sample_comanda_fallback.png"):
                frame_to_show = cv2.imread("sample_comanda_fallback.png")
                # Asegurarse de que la imagen de fallback se muestra si la cámara falla
                if frame_to_show is not None:
                    cv2.imshow('KDS Grill - Captura de Comanda', frame_to_show)
                    cv2.waitKey(1)
            return # Salir si no hay cámara y no hay fallback
    
    print(f"Webcam (ID {cap_id}) accedida con éxito.") # Simplificada la impresión

    # Opcional: Configurar resolución (comentar si causa problemas)
    # cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1920)
    # cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 1080)

    time.sleep(1) # Esperar un momento para que la cámara se inicialice

    ret, frame = cap.read() # Capturar un frame
    cap.release() # ¡MUY IMPORTANTE! Liberar la cámara

    if not ret:
        print("ERROR: No se pudo capturar el frame de la webcam. La imagen podría estar en negro o corrupta.")
        try:
            with open("sample_comanda_fallback.png", "rb") as image_file:
                encoded_image_string = base64.b64encode(image_file.read()).decode('utf-8')
            print("Usando imagen de fallback porque la captura falló.")
            if os.path.exists("sample_comanda_fallback.png"):
                frame_to_show = cv2.imread("sample_comanda_fallback.png")
        except FileNotFoundError:
            print("No se pudo capturar el frame y tampoco hay imagen de fallback.")
    else:
        frame_to_show = frame # El frame capturado se usará para mostrar y enviar
        # Codificar el frame capturado a Base64
        _, buffer = cv2.imencode('.png', frame)
        encoded_image_string = base64.b64encode(buffer).decode('utf-8')

    # --- 2. Mostrar la Captura en una Ventana ---
    if frame_to_show is not None:
        cv2.imshow('KDS Grill - Captura de Comanda', frame_to_show)
        cv2.waitKey(1) # Espera 1ms para permitir que la ventana se actualice

    order_counter += 1

    # --- 3. Simular Extracción de Datos y Enviar via WebSocket ---
    new_order_data = {
        'id': f'KDS-{order_counter:03d}',
        'table': (order_counter % 10) + 1,
        'startedAt': datetime.now().strftime('%H:%M'),
        'status': 'NEW',
        'initialDuration': 15 * 60,
        'timeRemaining': '15:00',
        'image': f'data:image/png;base64,{encoded_image_string}' if encoded_image_string else ''
    }

    print(f"Emitiendo nueva orden: {new_order_data['id']}")
    socketio.emit('new_order', new_order_data)

# --- Rutas HTTP Básicas (sin cambios) ---
@app.route('/')
def index():
    return "KDS Grill Backend - WebSockets Active"

@app.route('/add_mock_order')
def add_mock_order():
    with app.app_context():
        capture_and_send_order()
    return "Mock order added via WebSocket!"

# --- Eventos de WebSocket (sin cambios) ---
@socketio.on('connect')
def test_connect(auth=None):
    print('Cliente conectado:', request.sid)

@socketio.on('disconnect')
def test_disconnect():
    print('Cliente desconectado:', request.sid)
    # Se recomienda dejar solo uno de los destroyAllWindows para evitar conflictos de hilos.
    # El principal al final del script es generalmente suficiente.
    # cv2.destroyAllWindows() # Lo quitamos de aquí, se mantiene al final del main.


@socketio.on('update_order_status')
def handle_update_order_status(data):
    order_id = data.get('order_id')
    new_status = data.get('status')
    print(f"Recibida actualización de orden {order_id} a estado {new_status} desde el frontend.")

# --- Hilo para simular órdenes automáticas (con webcam) ---
def auto_order_generator():
    while True:
        # Asegúrate de que el contexto de la aplicación esté activo para emitir
        with app.app_context():
            capture_and_send_order()
        time.sleep(10) # Simula una nueva orden cada 10 segundos

# Iniciar el generador de órdenes automáticas en un hilo separado
order_thread = threading.Thread(target=auto_order_generator)
order_thread.daemon = True
order_thread.start()


if __name__ == '__main__':
    print("Iniciando Flask-SocketIO server...")
    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=True)
    except Exception as e:
        print(f"Error al iniciar el servidor Flask-SocketIO: {e}")
    finally:
        # Asegurarse de cerrar todas las ventanas de OpenCV al terminar la aplicación principal
        cv2.destroyAllWindows()