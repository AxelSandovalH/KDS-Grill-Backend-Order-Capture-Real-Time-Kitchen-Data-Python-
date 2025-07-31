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

# --- Configuración específica para webcam 0 ---
WEBCAM_ID = 0  # Forzar webcam 0
FRAME_WIDTH = 640
FRAME_HEIGHT = 480
FPS = 30

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
        print("ERROR (capture_and_send_order): No hay frame disponible del stream de webcam para la captura.")
        return

    # --- Depuración: Verificar si el frame es negro antes de enviar ---
    if np.sum(frame_to_process) < 1000: # Suma de píxeles muy baja = frame casi negro
        print("ADVERTENCIA (capture_and_send_order): El frame capturado para enviar parece ser negro o casi vacío.")
    else:
        print(f"Frame capturado correctamente. Suma de píxeles: {np.sum(frame_to_process)}")

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

# --- Función mejorada para inicializar la webcam 0 ---
def initialize_webcam():
    """Inicializa específicamente la webcam 0 con configuraciones optimizadas."""
    global camera
    
    print(f"Intentando conectar a webcam {WEBCAM_ID}...")
    
    # Intentar diferentes backends en orden de preferencia
    backends = [
        cv2.CAP_DSHOW,    # DirectShow (Windows)
        cv2.CAP_V4L2,     # Video4Linux2 (Linux)
        cv2.CAP_ANY       # Backend automático
    ]
    
    for backend in backends:
        try:
            camera = cv2.VideoCapture(WEBCAM_ID + backend)
            
            if camera.isOpened():
                # Configurar propiedades de la cámara
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_WIDTH)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_HEIGHT)
                camera.set(cv2.CAP_PROP_FPS, FPS)
                camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)  # Reducir buffer para menor latencia
                
                # Verificar que la cámara funcione leyendo un frame de prueba
                ret, test_frame = camera.read()
                if ret and test_frame is not None:
                    print(f"✓ Webcam {WEBCAM_ID} inicializada correctamente con backend {backend}")
                    print(f"  Resolución: {int(camera.get(cv2.CAP_PROP_FRAME_WIDTH))}x{int(camera.get(cv2.CAP_PROP_FRAME_HEIGHT))}")
                    print(f"  FPS: {camera.get(cv2.CAP_PROP_FPS)}")
                    return True
                else:
                    print(f"Webcam {WEBCAM_ID} abierta pero no pudo leer frame de prueba con backend {backend}")
                    camera.release()
                    camera = None
            else:
                print(f"No se pudo abrir webcam {WEBCAM_ID} con backend {backend}")
                
        except Exception as e:
            print(f"Error al inicializar webcam {WEBCAM_ID} con backend {backend}: {e}")
            if camera is not None:
                camera.release()
                camera = None
    
    return False

# --- Hilo para el Preview en Tiempo Real (Ventana CV2) ---
def webcam_preview_thread():
    global camera, last_webcam_frame

    # Inicializar webcam 0
    if not initialize_webcam():
        print("ERROR CRÍTICO: No se pudo inicializar la webcam 0. Terminando hilo de preview.")
        return

    print("Iniciando preview de webcam 0...")
    
    # Dar tiempo a la cámara para estabilizarse
    time.sleep(2)
    
    frame_count = 0
    last_fps_time = time.time()

    while True:
        try:
            with camera_lock: # Bloquear el acceso a la cámara mientras se lee
                if camera is None or not camera.isOpened():
                    print("ERROR: Cámara no disponible")
                    break
                    
                ret, frame = camera.read()
            
            if not ret or frame is None:
                print("ERROR: No se pudo leer frame de la webcam. Reintentando...")
                time.sleep(0.1)
                continue
            
            # Voltear horizontalmente para efecto espejo (opcional)
            frame = cv2.flip(frame, 1)
            
            # --- Depuración: Verificar calidad del frame ---
            frame_count += 1
            if frame_count % 30 == 0:  # Cada 30 frames (aprox. 1 segundo)
                current_time = time.time()
                fps = 30 / (current_time - last_fps_time)
                last_fps_time = current_time
                print(f"FPS actual: {fps:.1f}, Suma píxeles: {np.sum(frame)}")

            # Almacenar el último frame en el buffer global de forma segura
            with frame_buffer_lock:
                last_webcam_frame = frame.copy() # Guardar una copia del último frame

            # Agregar información en pantalla
            cv2.putText(frame, f'Webcam {WEBCAM_ID} - Presiona S para capturar', 
                       (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame, f'Ordenes capturadas: {order_counter}', 
                       (10, 60), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

            # Mostrar el feed en vivo
            cv2.imshow('KDS Grill - Estacion de Captura (S=Capturar, Q=Salir)', frame)
            
            # Escuchar pulsaciones de teclas
            key = cv2.waitKey(1) & 0xFF
            
            if key == ord('s') or key == ord('S'): # Tecla 's' o 'S' para snapshot/captura
                print("Tecla 'S' presionada. Disparando captura de comanda...")
                # Llamar a la función de captura en un hilo separado
                threading.Thread(target=capture_and_send_order, daemon=True).start()
                
                # Feedback visual
                cv2.putText(frame, 'CAPTURANDO...', (FRAME_WIDTH//2-100, FRAME_HEIGHT//2), 
                           cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 3)
                cv2.imshow('KDS Grill - Estacion de Captura (S=Capturar, Q=Salir)', frame)
                cv2.waitKey(500)  # Mostrar por 500ms
                
            elif key == ord('q') or key == ord('Q'): # Tecla 'q' o 'Q' para salir
                print("Tecla 'Q' presionada. Cerrando ventana de captura.")
                break
                
        except Exception as e:
            print(f"Error en el bucle de preview: {e}")
            time.sleep(0.1)
            continue

    # Liberar recursos al salir
    cleanup_camera()
    print("Hilo de preview de webcam terminado.")

def cleanup_camera():
    """Limpia y libera los recursos de la cámara."""
    global camera
    
    if camera is not None:
        with camera_lock:
            camera.release()
            camera = None
    
    cv2.destroyAllWindows()
    print("Recursos de cámara liberados.")

# --- Rutas HTTP de Flask ---
@app.route('/')
def index():
    return f"KDS Grill Backend - WebSockets Active - Webcam {WEBCAM_ID} - Órdenes: {order_counter}"

@app.route('/status')
def status():
    """Endpoint para verificar el estado del sistema."""
    camera_status = "Conectada" if camera is not None and camera.isOpened() else "Desconectada"
    return {
        'webcam_id': WEBCAM_ID,
        'camera_status': camera_status,
        'orders_captured': order_counter,
        'last_frame_available': last_webcam_frame is not None
    }

# --- Eventos de WebSocket ---
@socketio.on('connect')
def test_connect(auth=None):
    print('Cliente conectado:', request.sid)
    # Enviar estado inicial
    emit('system_status', {
        'webcam_id': WEBCAM_ID,
        'orders_captured': order_counter
    })

@socketio.on('disconnect')
def test_disconnect():
    print('Cliente desconectado:', request.sid)

@socketio.on('update_order_status')
def handle_update_order_status(data):
    order_id = data.get('order_id')
    new_status = data.get('status')
    print(f"Recibida actualización de orden {order_id} a estado {new_status} desde el frontend.")

@socketio.on('capture_order')
def handle_manual_capture():
    """Permitir captura manual desde el frontend."""
    print("Captura manual solicitada desde el frontend.")
    threading.Thread(target=capture_and_send_order, daemon=True).start()

# Bloque de ejecución principal
if __name__ == '__main__':
    print("=== KDS Grill - Sistema de Captura de Órdenes ===")
    print(f"Configurado para usar webcam {WEBCAM_ID}")
    print("Iniciando Flask-SocketIO server...")
    
    # Iniciar el hilo del preview de la webcam
    preview_thread = threading.Thread(target=webcam_preview_thread, daemon=True)
    preview_thread.start()

    try:
        socketio.run(app, host='0.0.0.0', port=5000, debug=True, allow_unsafe_werkzeug=True)
    except KeyboardInterrupt:
        print("\nInterrupción del usuario detectada...")
    except Exception as e:
        print(f"Error al iniciar el servidor Flask-SocketIO: {e}")
    finally:
        print("Cerrando aplicación...")
        cleanup_camera()
        print("Aplicación Flask-SocketIO terminada.")