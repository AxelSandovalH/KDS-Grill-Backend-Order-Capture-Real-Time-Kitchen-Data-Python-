# app.py
from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
import base64
import cv2 # Para la webcam
import time
import os
import threading
from datetime import datetime
import numpy as np # Necesario para np.sum() para depuración

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

# Variable para controlar si el hilo de preview ya fue iniciado
preview_thread_started = False
preview_thread_lock = threading.Lock()

# --- Nombre del archivo de video de fallback (para pruebas sin webcam) ---
VIDEO_FALLBACK_PATH = "sample_video_for_preview.mp4" 

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
        print("ERROR (capture_and_send_order): No hay frame disponible del stream (webcam o video) para la captura.")
        # Fallback a una imagen estática si no hay frame en vivo disponible
        try:
            with open("sample_comanda_fallback.png", "rb") as image_file:
                encoded_image_string = base64.b64encode(image_file.read()).decode('utf-8')
            print("Usando imagen de fallback estática porque no hay frame de webcam/video.")
        except FileNotFoundError:
            print("No hay frame de webcam/video y tampoco imagen de fallback. La orden se enviará sin imagen.")
            return

    else:
        # --- Depuración: Verificar si el frame es negro antes de enviar ---
        if np.sum(frame_to_process) < 1000: # Suma de píxeles muy baja = frame casi negro
            print("ADVERTENCIA (capture_and_send_order): El frame capturado para enviar parece ser negro o casi vacío.")
        # --- Fin Depuración ---

        # Procesar el frame capturado
        _, buffer = cv2.imencode('.png', frame_to_process)
        encoded_image_string = base64.b64encode(buffer).decode('utf-8')

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
    with app.app_context():
        socketio.emit('new_order', new_order_data)

# --- Hilo para el Preview en Tiempo Real (Ventana CV2) ---
def webcam_preview_thread():
    global camera, last_webcam_frame

    print("Iniciando hilo de preview de webcam...")
    
    cap_attempts = [
        (0, cv2.CAP_DSHOW),
        (1, cv2.CAP_DSHOW),
        (0, cv2.CAP_ANY),
        (1, cv2.CAP_ANY)
    ]
    
    camera_opened = False
    for cap_id, backend in cap_attempts:
        print(f"Intentando abrir cámara ID {cap_id} con backend {backend}...")
        camera = cv2.VideoCapture(cap_id, backend)
        
        # Configurar propiedades de la cámara para mejor rendimiento
        if camera.isOpened():
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
            camera.set(cv2.CAP_PROP_FPS, 30)
            
            # Hacer una lectura de prueba
            ret, test_frame = camera.read()
            if ret and test_frame is not None:
                print(f"Webcam (ID {cap_id}, Backend {backend}) accedida exitosamente para preview en tiempo real.")
                camera_opened = True
                break
            else:
                print(f"Cámara ID {cap_id} abrió pero no puede leer frames.")
                camera.release()
        else:
            print(f"No se pudo acceder a la webcam con ID {cap_id} y Backend {backend}.")

    # --- FALLBACK A VIDEO SI LA CÁMARA REAL NO FUNCIONA ---
    if not camera_opened:
        if os.path.exists(VIDEO_FALLBACK_PATH):
            print(f"Webcam no disponible. Usando archivo de video de fallback: {VIDEO_FALLBACK_PATH}")
            camera = cv2.VideoCapture(VIDEO_FALLBACK_PATH)
            if not camera.isOpened():
                print(f"ERROR: No se pudo abrir el archivo de video: {VIDEO_FALLBACK_PATH}")
                return # Si el video tampoco funciona, no hay preview
            else:
                camera_opened = True
        else:
            print(f"ERROR: No se pudo acceder a la webcam en ningún intento y el archivo '{VIDEO_FALLBACK_PATH}' no existe.")
            return # No se puede iniciar el preview sin cámara o video

    # Dar tiempo a la cámara/video para inicializarse
    time.sleep(1)

    frame_count = 0
    while True:
        try:
            with camera_lock: # Bloquear el acceso a la cámara mientras se lee
                ret, frame = camera.read()
            
            if not ret or frame is None:
                # Si es un video, y se acaba, reiniciar
                if camera_opened and hasattr(camera, 'get'):
                    current_frame = camera.get(cv2.CAP_PROP_POS_FRAMES)
                    total_frames = camera.get(cv2.CAP_PROP_FRAME_COUNT)
                    if current_frame >= total_frames - 1:
                        print("Video de fallback terminado, reiniciando...")
                        camera.set(cv2.CAP_PROP_POS_FRAMES, 0) # Volver al inicio del video
                        continue
                else:
                    print("ERROR (webcam_preview_thread): Falló la lectura del frame del preview. Reintentando...")
                    time.sleep(0.1)
                    continue
            
            frame_count += 1
            
            # --- Depuración: Verificar si el frame del preview es negro ---
            frame_sum = np.sum(frame)
            if frame_sum < 1000: # Suma de píxeles muy baja = frame casi negro
                print(f"ADVERTENCIA (webcam_preview_thread): Frame #{frame_count} parece ser negro o casi vacío (suma: {frame_sum}).")
            # --- Fin Depuración ---

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
                time.sleep(0.5) # Pequeña pausa para evitar multiples capturas si se mantiene 'S'
            elif key == ord('q'): # Tecla 'q' para salir
                print("Tecla 'Q' presionada. Cerrando ventana de captura.")
                break # Salir del bucle del preview
                
        except Exception as e:
            print(f"Error en el bucle de preview: {e}")
            time.sleep(0.1)
            continue

    # Liberar la cámara y destruir la ventana de OpenCV al salir del hilo
    try:
        if camera is not None:
            camera.release()
        cv2.destroyAllWindows()
        print("Hilo de preview de webcam terminado correctamente.")
    except Exception as e:
        print(f"Error al cerrar recursos de cámara: {e}")

def start_preview_thread():
    """Función para iniciar el hilo de preview de forma segura"""
    global preview_thread_started
    
    with preview_thread_lock:
        if not preview_thread_started:
            print("Iniciando hilo de preview de webcam...")
            preview_thread = threading.Thread(target=webcam_preview_thread, daemon=True)
            preview_thread.start()
            preview_thread_started = True
            return True
        else:
            print("Hilo de preview ya está en ejecución.")
            return False

# --- Rutas HTTP de Flask (simples) ---
@app.route('/')
def index():
    return "KDS Grill Backend - WebSockets Active"

@app.route('/start_preview')
def start_preview():
    """Ruta para iniciar manualmente el preview si es necesario"""
    if start_preview_thread():
        return "Preview iniciado correctamente"
    else:
        return "Preview ya está en ejecución"

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
    
    # Verificar si estamos en el proceso principal (no en el reloader de Werkzeug)
    # Esto evita que se ejecuten múltiples hilos de preview cuando debug=True
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print("Proceso principal detectado - iniciando hilo de preview")
        start_preview_thread()
    else:
        print("Proceso de reloader detectado - saltando inicio de hilo de preview")

    try:
        # Usar use_reloader=False para evitar problemas con hilos duplicados
        # pero mantener debug=True para otras funcionalidades de desarrollo
        socketio.run(
            app, 
            host='0.0.0.0', 
            port=5000, 
            debug=True, 
            use_reloader=False,  # Esta es la clave para evitar ventanas duplicadas
            allow_unsafe_werkzeug=True
        )
    except Exception as e:
        print(f"Error al iniciar el servidor Flask-SocketIO: {e}")
    finally:
        # Asegurarse de cerrar todas las ventanas de OpenCV cuando la aplicación principal termina
        try:
            cv2.destroyAllWindows()
            # Si la cámara se liberó en el hilo de preview, esta línea no hace nada si ya es None
            if camera is not None:
                camera.release()
            print("Recursos de cámara liberados correctamente.")
        except Exception as e:
            print(f"Error al liberar recursos: {e}")
        print("Aplicación Flask-SocketIO terminada.")