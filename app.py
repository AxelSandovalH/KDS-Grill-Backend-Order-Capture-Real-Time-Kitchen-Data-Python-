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

# --- Configuración específica de la webcam ---
WEBCAM_ID = 0 # Forzar webcam 0
# Resolución base que se intenta obtener de la cámara (ej. 640x480, 1280x720, 1920x1080)
# La cámara puede dar una resolución diferente a la solicitada, el código se adaptará.
BASE_FRAME_WIDTH = 640
BASE_FRAME_HEIGHT = 480 # Usaremos esta altura como ancla para el aspecto 9:16
FPS = 30

# --- Configuración de recorte a formato de celular (vertical) ---
# Aspect ratio objetivo: Ancho / Alto (ej. 9/16 = 0.5625)
TARGET_ASPECT_RATIO = 7 / 8

# Dimensiones finales del frame después del recorte/reescalado para asegurar el aspecto 9:16
# Si la altura es 480, el ancho para 9:16 será 480 * (9/16) = 270
FINAL_OUTPUT_WIDTH = int(BASE_FRAME_HEIGHT * TARGET_ASPECT_RATIO)
FINAL_OUTPUT_HEIGHT = BASE_FRAME_HEIGHT


# --- Nombre del archivo de video de fallback (para pruebas sin webcam) ---
VIDEO_FALLBACK_PATH = "sample_video_for_preview.mp4"
# --- Imagen de fallback estática si no hay frames de video/webcam ---
STATIC_FALLBACK_IMAGE = "sample_comanda_fallback.png"

# --- Función para capturar y enviar la orden (disparada por la tecla 'S' en la ventana CV2) ---
def capture_and_send_order():
    """Toma el frame actual del buffer, lo codifica y envía via WebSocket."""
    global order_counter, last_webcam_frame

    print("DEBUG: capture_and_send_order() - Iniciada.")
    encoded_image_string = ""
    frame_to_process = None

    # Acceder al último frame del buffer de forma segura
    with frame_buffer_lock:
        if last_webcam_frame is not None and np.sum(last_webcam_frame) > 1000: # Solo si el frame no es negro
            frame_to_process = last_webcam_frame.copy()
        else:
            print("DEBUG: capture_and_send_order() - last_webcam_frame es None o negro. No hay frame válido del preview.")

    # Si no se pudo obtener un frame válido del stream, intentar usar la imagen de fallback estática
    if frame_to_process is None or np.sum(frame_to_process) < 1000: # También si el frame es negro
        print("ERROR (capture_and_send_order): Frame de stream inválido/negro. Intentando fallback estático.")
        if os.path.exists(STATIC_FALLBACK_IMAGE):
            try:
                fallback_img_data = cv2.imread(STATIC_FALLBACK_IMAGE)
                if fallback_img_data is not None:
                    # Redimensionar la imagen de fallback al tamaño FINAL CROPPEADO
                    # Usar un tamaño consistente con el formato del celular
                    fallback_img_data = cv2.resize(fallback_img_data, (FINAL_OUTPUT_WIDTH, FINAL_OUTPUT_HEIGHT))
                    frame_to_process = fallback_img_data
                    print(f"Usando imagen de fallback estática '{STATIC_FALLBACK_IMAGE}'.")
                else:
                    print(f"ADVERTENCIA: Fallback estático '{STATIC_FALLBACK_IMAGE}' no pudo cargarse con cv2.imread().")
            except Exception as e:
                print(f"ADVERTENCIA: Error al cargar fallback estático '{STATIC_FALLBACK_IMAGE}': {e}")
        else:
            print(f"ADVERTENCIA: Archivo de fallback estático '{STATIC_FALLBACK_IMAGE}' no encontrado.")

    # Si después de todo, frame_to_process sigue siendo None o negro, no hay nada que enviar
    if frame_to_process is None or np.sum(frame_to_process) < 1000:
        print("ERROR CRÍTICO (capture_and_send_order): No se pudo obtener ningún frame válido (ni de stream ni de fallback). Saliendo sin emitir.")
        return

    # --- Depuración: Verificar el frame FINAL ANTES DE ENVIAR ---
    if np.sum(frame_to_process) < 1000:
        print("ADVERTENCIA (capture_and_send_order): EL FRAME FINAL A ENVIAR SIGUE SIENDO NEGRO O CASI VACÍO.")
    else:
        print(f"DEBUG: capture_and_send_order() - Frame FINAL para enviar no es negro. Suma de píxeles: {np.sum(frame_to_process)}")

    # Procesar el frame capturado
    _, buffer = cv2.imencode('.png', frame_to_process)
    encoded_image_string = base64.b64encode(buffer.tobytes()).decode('utf-8')

    order_counter += 1
    new_order_data = {
        'id': f'KDS-{order_counter:03d}',
        # CAMBIO AQUÍ: Eliminamos la operación de módulo.
        # Ahora, el número de mesa será simplemente el order_counter actual.
        'table': order_counter,
        'startedAt': datetime.now().strftime('%H:%M'),
        'status': 'NEW',
        'initialDuration': 15 * 60,
        'timeRemaining': '15:00',
        'image': f'data:image/png;base64,{encoded_image_string}' if encoded_image_string else ''
    }

    print(f"DEBUG: capture_and_send_order() - Preparado para emitir orden {new_order_data['id']}.")
    with app.app_context():
        socketio.emit('new_order', new_order_data)
        print(f"DEBUG: capture_and_send_order() - Orden {new_order_data['id']} emitida.")

# --- Función para inicializar la webcam/video ---
def initialize_webcam():
    """Inicializa la webcam 0 con configuraciones o usa fallback de video."""
    global camera

    print(f"Intentando conectar a webcam {WEBCAM_ID}...")

    cap_attempts = [
        (WEBCAM_ID, cv2.CAP_DSHOW), # DirectShow (Windows)
        (WEBCAM_ID, cv2.CAP_V4L2),  # Video4Linux2 (Linux)
        (WEBCAM_ID, cv2.CAP_ANY) # Backend automático
    ]

    camera_opened = False
    for current_id, backend in cap_attempts:
        print(f"  Intento: ID {current_id}, Backend {backend}...")
        camera = cv2.VideoCapture(current_id + backend)

        if camera.isOpened():
            # Configurar propiedades de la cámara a la resolución BASE
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, BASE_FRAME_WIDTH)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, BASE_FRAME_HEIGHT)
            camera.set(cv2.CAP_PROP_FPS, FPS)
            camera.set(cv2.CAP_PROP_BUFFERSIZE, 1) # Reducir buffer para menor latencia

            # Verificar que la cámara funcione leyendo un frame de prueba que no sea negro
            ret, test_frame = camera.read()
            if ret and test_frame is not None and np.sum(test_frame) > 1000:
                print(f"✓ Webcam {current_id}, Backend {backend} accedida correctamente.")
                print(f"  Resolución (base): {int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))}, FPS: {camera.get(cv2.CAP_PROP_FPS)}")
                camera_opened = True
                break
            else:
                print(f"  Cámara ID {current_id} abrió pero no pudo leer frame de prueba válido/no negro con backend {backend}.")
                camera.release()
                camera = None
        else:
            print(f"  No se pudo abrir webcam {current_id} con backend {backend}.")

    # --- FALLBACK A VIDEO SI LA CÁMARA REAL NO FUNCIONA ---
    if not camera_opened:
        if os.path.exists(VIDEO_FALLBACK_PATH):
            print(f"Webcam no disponible. Usando archivo de video de fallback: {VIDEO_FALLBACK_PATH}")
            camera = cv2.VideoCapture(VIDEO_FALLBACK_PATH)
            if not camera.isOpened():
                print(f"ERROR: No se pudo abrir el archivo de video: {VIDEO_FALLBACK_PATH}")
                return False
            else:
                # Configurar propiedades del video de fallback para que coincida con la resolución BASE
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, BASE_FRAME_WIDTH)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, BASE_FRAME_HEIGHT)
                camera_opened = True
        else:
            print(f"ERROR: No se pudo acceder a la webcam en ningún intento y el archivo '{VIDEO_FALLBACK_PATH}' no existe.")
            return False

    return camera_opened

# --- Hilo para el Preview en Tiempo Real (Ventana CV2) ---
def webcam_preview_thread():
    global camera, last_webcam_frame

    if not initialize_webcam():
        print("ERROR CRÍTICO: No se pudo inicializar ninguna fuente de video. Terminando hilo de preview.")
        return

    print("Iniciando preview de video...")

    time.sleep(2) # Dar tiempo a la cámara/video para estabilizarse

    frame_count = 0
    last_fps_time = time.time()

    while True:
        try:
            with camera_lock: # Bloquear el acceso a la cámara mientras se lee
                if camera is None or not camera.isOpened():
                    print("ERROR (webcam_preview_thread): Cámara/Video no disponible en bucle principal. Reintentando...")
                    if initialize_webcam(): # Intenta re-inicializar
                        print("Cámara re-inicializada con éxito.")
                        continue
                    else:
                        break # Salir si no se puede re-inicializar

                ret, frame = camera.read()

            if not ret or frame is None:
                # Si es un video, y se acaba, reiniciar
                if hasattr(camera, 'get') and camera.get(cv2.CAP_PROP_POS_FRAMES) == camera.get(cv2.CAP_PROP_FRAME_COUNT):
                    print("Video de fallback terminado, reiniciando...")
                    camera.set(cv2.CAP_PROP_POS_FRAMES, 0) # Volver al inicio del video
                    continue
                else:
                    print("ERROR (webcam_preview_thread): Falló la lectura del frame del preview. Reintentando...")
                    time.sleep(0.1)
                    continue

            # --- Procesamiento del frame para obtener el formato de celular ---
            h, w, _ = frame.shape # Obtener dimensiones actuales del frame

            # Calcular el ancho que la imagen debería tener para el aspect ratio objetivo, manteniendo la altura
            # Si el frame original es 640x480 (4:3), y el objetivo es 9:16 (vertical),
            # entonces para una altura de 480, el ancho objetivo es 480 * (9/16) = 270.
            # Recortaremos 640 - 270 = 370px, 185px de cada lado.
            target_w_for_h = int(h * TARGET_ASPECT_RATIO)

            frame_processed = frame.copy() # Inicializar con el frame completo

            # Si el ancho original es mayor que el ancho objetivo, recortamos los lados
            if w > target_w_for_h:
                crop_start_x = (w - target_w_for_h) // 2
                crop_end_x = crop_start_x + target_w_for_h
                frame_processed = frame[:, crop_start_x:crop_end_x]
            # If original width is smaller than target width (e.g., already narrower than 9:16),
            # or if it's already exactly 9:16 but smaller, it will simply be rescaled in the next step.

            # Ensure the processed_frame has the expected FINAL OUTPUT dimensions
            # This is necessary if the camera doesn't give exact resolution or if cropping didn't scale perfectly.
            # And to standardize the output size for the frontend.
            if frame_processed.shape[1] != FINAL_OUTPUT_WIDTH or frame_processed.shape[0] != FINAL_OUTPUT_HEIGHT:
                frame_processed = cv2.resize(frame_processed, (FINAL_OUTPUT_WIDTH, FINAL_OUTPUT_HEIGHT))


            # --- Debug: Only store frame if it's not black ---
            if np.sum(frame_processed) > 1000: # If frame has pixels (is not black)
                with frame_buffer_lock:
                    last_webcam_frame = frame_processed.copy() # Save a copy of the PROCESSED frame
            else:
                print("WARNING (webcam_preview_thread): Preview frame appears to be black or nearly empty (will not be buffered).")

            # Move FPS calculation and print outside the if so it's always done
            frame_count += 1
            if frame_count % 30 == 0:
                current_time = time.time()
                fps = 30 / (current_time - last_fps_time)
                last_fps_time = current_time
                print(f"DEBUG: Preview FPS: {fps:.1f}, Current pixel sum: {np.sum(frame_processed)}") # Show sum of PROCESSED frame

            # Add screen information (to the PROCESSED frame for the window)
            # Ensure coordinates are relative to the processed_frame size
            cv2.putText(frame_processed, f'Cam ID {WEBCAM_ID}',
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame_processed, f'Orders captured: {order_counter}',
                       (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame_processed, f'Frame sum: {np.sum(frame_processed)}',
                       (10, 90), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame_processed, f'Press S to Capture / Q to Exit',
                       (10, frame_processed.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # Show the live feed (the PROCESSED frame)
            cv2.imshow('KDS Grill - Capture Station', frame_processed)

            # Listen for key presses: 's' for snapshot, 'q' to exit preview window
            key = cv2.waitKey(1) & 0xFF

            if key == ord('s') or key == ord('S'):
                print("Key 'S' pressed. Triggering order capture...")
                threading.Thread(target=capture_and_send_order, daemon=True).start()
                time.sleep(0.5)
            elif key == ord('q') or key == ord('Q'):
                print("Key 'Q' pressed. Closing capture window.")
                break

        except Exception as e:
            print(f"Error in preview loop: {e}")
            time.sleep(0.1)
            continue

    cleanup_camera()
    print("Webcam preview thread terminated.")

def cleanup_camera():
    """Cleans up and releases camera resources."""
    global camera

    if camera is not None:
        with camera_lock:
            camera.release()
            camera = None

    cv2.destroyAllWindows()
    print("Camera resources released.")

# Functions to safely start/stop preview thread (for Werkzeug reloader)
def start_preview_thread_safe():
    global preview_thread_started
    with preview_thread_lock:
        if not preview_thread_started:
            print("Starting webcam preview thread...")
            preview_thread = threading.Thread(target=webcam_preview_thread, daemon=True)
            preview_thread.start()
            preview_thread_started = True
            return True
        else:
            print("Preview thread already running.")
            return False

# Flask HTTP routes (simple)
@app.route('/')
def index():
    return "KDS Grill Backend - WebSockets Active"

# WebSocket events (unchanged)
@socketio.on('connect')
def test_connect(auth=None):
    print('Client connected:', request.sid)

@socketio.on('disconnect')
def test_disconnect():
    print('Client disconnected:', request.sid)

@socketio.on('update_order_status')
def handle_update_order_status(data):
    order_id = data.get('order_id')
    new_status = data.get('status')
    print(f"Received order update {order_id} to status {new_status} from frontend.")

@socketio.on('capture_order')
def handle_manual_capture():
    """Allow manual capture from frontend."""
    print("Manual capture requested from frontend.")
    threading.Thread(target=capture_and_send_order, daemon=True).start()

# Main execution block
if __name__ == '__main__':
    print("=== KDS Grill - Order Capture System ===")
    print(f"Configured to use webcam {WEBCAM_ID}")
    print("Starting Flask-SocketIO server...")

    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print("Main process detected - starting preview thread safely.")
        start_preview_thread_safe()
    else:
        print("Reloader process detected - skipping preview thread startup.")

    try:
        socketio.run(
            app,
            host='0.0.0.0',
            port=5000,
            debug=True,
            use_reloader=False,
            allow_unsafe_werkzeug=True
        )
    except KeyboardInterrupt:
        print("\nUser interruption detected...")
    except Exception as e:
        print(f"Error starting Flask-SocketIO server: {e}")
    finally:
        print("Closing application...")
        cleanup_camera()
        print("Flask-SocketIO application terminated.")