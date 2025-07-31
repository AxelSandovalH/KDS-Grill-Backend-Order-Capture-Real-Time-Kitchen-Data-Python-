# app.py
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import base64
import cv2 # Para la webcam
import time
import os
import threading
from datetime import datetime

# Configuración de Flask y SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tu_clave_secreta_aqui_CAMBIAME' # ¡CAMBIA ESTO EN PRODUCCIÓN!
socketio = SocketIO(app, cors_allowed_origins="*") # Permite conexión desde cualquier origen (cambiar para producción)

# Variables globales
order_counter = 0

# Objeto global para la cámara y un lock para acceso seguro entre hilos
camera = None
camera_lock = threading.Lock() # Para asegurar acceso seguro a la cámara

# Variable global para almacenar el último frame del stream
last_webcam_frame = None
frame_buffer_lock = threading.Lock() # Para proteger el acceso a last_webcam_frame

# --- Función para capturar y enviar la orden (disparada por la tecla 'S' en la ventana CV2) ---
def capture_and_send_order():
    """Toma el frame actual del buffer, lo codifica y envía via WebSocket."""
    global order_counter, last_webcam_frame

    encoded_image_string = ""
    frame_to_process = None

    # Acceder al último frame del buffer de forma segura
    with frame_buffer_lock:
        if last_webcam_frame is not None:
            frame_to_process = last_webcam_frame.copy() # Obtener una copia para procesar

    if frame_to_process is None:
        print("ERROR: No hay frame disponible del stream de la webcam para la captura.")
        # Fallback a una imagen estática si no hay frame en vivo disponible
        try:
            with open("sample_comanda_fallback.png", "rb") as image_file:
                encoded_image_string = base64.b64encode(image_file.read()).decode('utf-8')
            print("Usando imagen de fallback porque no hay frame de webcam.")
        except FileNotFoundError:
            print("No hay frame de webcam y tampoco imagen de fallback. La orden se enviará sin imagen.")
        return

    # Procesar el frame capturado
    # Usamos .png para mayor calidad y porque tu frontend ya espera .png
    _, buffer = cv2.imencode('.png', frame_to_process)
    encoded_image_string = base64.b64encode(buffer).decode('utf-8')

    # --- Mostrar el Frame Capturado en una ventana separada de CV2 (brevemente) ---
    cv2.imshow('KDS Grill - Captura Realizada', frame_to_process)
    cv2.waitKey(100) # Mostrar por 100ms
    cv2.destroyWindow('KDS Grill - Captura Realizada') # Cerrar automáticamente

    order_counter += 1
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
    # Es crucial que esta función se ejecute dentro del contexto de la aplicación Flask
    # para que socketio.emit() funcione correctamente.
    with app.app_context():
        socketio.emit('new_order', new_order_data)

# --- Hilo para el Preview en Tiempo Real de la Webcam (Ventana CV2) ---
def webcam_preview_thread():
    global camera, last_webcam_frame

    # Intentar abrir la cámara si no está abierta
    if camera is None or not camera.isOpened():
        cap_id = 0
        camera = cv2.VideoCapture(cap_id)
        if not camera.isOpened():
            print(f"ERROR: No se pudo acceder a la webcam con ID {cap_id}. Intentando con ID 1...")
            cap_id = 1
            camera = cv2.VideoCapture(cap_id)
            if not camera.isOpened():
                print(f"ERROR: Tampoco se pudo acceder a la webcam con ID {cap_id}. El preview no se iniciará.")
                return # No se puede iniciar el preview sin cámara

        print(f"Webcam (ID {cap_id}) accedida para preview en tiempo real.")

        # Opcional: Configurar resolución para el preview (menor para rendimiento, mayor para detalle)
        # camera.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        # camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)

    # Dar tiempo a la cámara para inicializarse y ajustar exposición
    time.sleep(1)

    while True:
        with camera_lock: # Bloquear el acceso a la cámara mientras se lee
            ret, frame = camera.read()
        
        if not ret:
            print("ERROR: Falló la lectura del frame del preview. Reintentando...")
            time.sleep(0.1)
            continue

        # Almacenar el último frame en el buffer global de forma segura
        with frame_buffer_lock:
            last_webcam_frame = frame.copy() # Guardar una copia del último frame

        # Mostrar el feed en vivo
        cv2.imshow('KDS Grill - Estacion de Captura (Presiona "S" para Capturar, "Q" para Salir)', frame)
        
        # Escuchar pulsaciones de teclas: 's' para snapshot, 'q' para salir de la ventana de preview
        key = cv2.waitKey(1) & 0xFF # Esperar 1ms, obtener pulsación de tecla
        
        if key == ord('s'): # Tecla 's' para snapshot/captura
            print("Tecla 'S' presionada. Disparando captura de comanda...")
            # Llamar a la función de captura en un hilo separado para no bloquear el preview
            threading.Thread(target=capture_and_send_order, daemon=True).start()
        elif key == ord('q'): # Tecla 'q' para salir
            print("Tecla 'Q' presionada. Cerrando ventana de captura.")
            break # Salir del bucle del preview

    # Liberar la cámara y destruir la ventana de OpenCV al salir del hilo
    cap.release()
    cv2.destroyWindow('KDS Grill - Estacion de Captura (Presiona "S" para Capturar, "Q" para Salir)')
    print("Hilo de preview de webcam terminado.")


# --- Rutas HTTP de Flask (simples, ya no para la captura) ---
@app.route('/')
def index():
    return "KDS Grill Backend - WebSockets Active"

# Eliminamos el endpoint /capture_button_press ya que la captura se hace desde la ventana CV2

# --- Eventos de WebSocket (sin cambios) ---
@socketio.on('connect')
def test_connect(auth=None):
    print('Cliente conectado:', request.sid)

@socketio.on('disconnect')
def test_disconnect():
    print('Cliente desconectado:', request.sid)

@socketio.on('update_order_status')
def handle_update_order_status(data):
    order_id = data.get('order_id')
    new_status = data.get('status')
    print(f"Recibida actualización de orden {order_id} a estado {new_status} desde el frontend.")

# Bloque de ejecución principal
if __name__ == '__main__':
    print("Iniciando Flask-SocketIO server...")
    
    # Iniciar el hilo del preview de la webcam
    preview_thread = threading.Thread(target=webcam_preview_thread, daemon=True)
    preview_thread.start()

    try:
        # allow_unsafe_werkzeug=True para evitar warnings con threading en modo debug
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
    except Exception as e:
        print(f"Error al iniciar el servidor Flask-SocketIO: {e}")
    finally:
        # Asegurarse de cerrar todas las ventanas de OpenCV cuando la aplicación principal termina
        cv2.destroyAllWindows()
        # Si la cámara se abrió globalmente, liberarla al final
        if camera is not None:
            camera.release()
        print("Aplicación Flask-SocketIO terminada.")