# app.py

from flask import Flask, render_template, request
from flask_socketio import SocketIO, emit
from flask_sqlalchemy import SQLAlchemy
import base64
import cv2
import time
import os
import threading
from datetime import datetime
import numpy as np

# Configuración de Flask y SocketIO
app = Flask(__name__)
app.config['SECRET_KEY'] = 'tu_clave_secreta_aqui_CAMBIAME'
socketio = SocketIO(app, cors_allowed_origins="*")

# --- Configuración de la base de datos SQLite ---
# Nombre del archivo de la base de datos
DB_FILE = 'orders.db'
app.config['SQLALCHEMY_DATABASE_URI'] = f'sqlite:///{DB_FILE}'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# Modelo de la tabla de órdenes
class Order(db.Model):
    __tablename__ = 'orders'
    id = db.Column(db.String(50), primary_key=True)
    table = db.Column(db.Integer, nullable=False)
    started_at = db.Column(db.String(20), nullable=False)
    status = db.Column(db.String(20), nullable=False)
    initial_duration = db.Column(db.Integer, nullable=False)
    image_data = db.Column(db.Text, nullable=False)  # Guardaremos la imagen como un string Base64

    def __repr__(self):
        return f'<Order {self.id}>'

    def to_dict(self):
        return {
            'id': self.id,
            'table': self.table,
            'startedAt': self.started_at,
            'status': self.status,
            'initialDuration': self.initial_duration,
            'image': self.image_data,
        }
# --- Fin de la configuración de la base de datos ---

# Variables globales para la webcam y un lock para acceso seguro entre hilos
camera = None
camera_lock = threading.Lock()
last_webcam_frame = None
frame_buffer_lock = threading.Lock()
preview_thread_started = False
preview_thread_lock = threading.Lock()

# Configuración específica de la webcam
WEBCAM_ID = 0
BASE_FRAME_WIDTH = 640
BASE_FRAME_HEIGHT = 480
FPS = 30
TARGET_ASPECT_RATIO = 7 / 8
FINAL_OUTPUT_WIDTH = int(BASE_FRAME_HEIGHT * TARGET_ASPECT_RATIO)
FINAL_OUTPUT_HEIGHT = BASE_FRAME_HEIGHT
VIDEO_FALLBACK_PATH = "sample_video_for_preview.mp4"
STATIC_FALLBACK_IMAGE = "sample_comanda_fallback.png"

def capture_and_send_order():
    """Toma el frame actual del buffer, lo codifica y envía via WebSocket, y guarda en DB."""
    encoded_image_string = ""
    frame_to_process = None

    with frame_buffer_lock:
        if last_webcam_frame is not None and np.sum(last_webcam_frame) > 1000:
            frame_to_process = last_webcam_frame.copy()
        else:
            print("DEBUG: capture_and_send_order() - last_webcam_frame es None o negro. Intentando fallback estático.")

    if frame_to_process is None or np.sum(frame_to_process) < 1000:
        if os.path.exists(STATIC_FALLBACK_IMAGE):
            try:
                fallback_img_data = cv2.imread(STATIC_FALLBACK_IMAGE)
                if fallback_img_data is not None:
                    fallback_img_data = cv2.resize(fallback_img_data, (FINAL_OUTPUT_WIDTH, FINAL_OUTPUT_HEIGHT))
                    frame_to_process = fallback_img_data
                    print(f"Usando imagen de fallback estática '{STATIC_FALLBACK_IMAGE}'.")
                else:
                    print(f"ADVERTENCIA: Fallback estático '{STATIC_FALLBACK_IMAGE}' no pudo cargarse con cv2.imread().")
            except Exception as e:
                print(f"ADVERTENCIA: Error al cargar fallback estático '{STATIC_FALLBACK_IMAGE}': {e}")
        else:
            print(f"ADVERTENCIA: Archivo de fallback estático '{STATIC_FALLBACK_IMAGE}' no encontrado.")
   
    if frame_to_process is None or np.sum(frame_to_process) < 1000:
        print("ERROR CRÍTICO (capture_and_send_order): No se pudo obtener ningún frame válido. Saliendo sin emitir.")
        return

    _, buffer = cv2.imencode('.png', frame_to_process)
    encoded_image_string = 'data:image/png;base64,' + base64.b64encode(buffer.tobytes()).decode('utf-8')

    with app.app_context():
        # --- Lógica de persistencia en la base de datos ---
        current_orders = Order.query.all()
        # El order_counter se basa en la cantidad de órdenes en la DB + 1
        order_counter = len(current_orders) + 1
        
        new_order_data = Order(
            id=f'KDS-{order_counter:03d}',
            table=order_counter,
            started_at=datetime.now().strftime('%H:%M'),
            status='NEW',
            initial_duration=15 * 60,
            image_data=encoded_image_string
        )

        db.session.add(new_order_data)
        db.session.commit()
        # --- Fin de la lógica de persistencia en la base de datos ---

        print(f"DEBUG: capture_and_send_order() - Preparado para emitir y guardar orden {new_order_data.id}.")
        socketio.emit('new_order', new_order_data.to_dict())
        print(f"DEBUG: capture_and_send_order() - Orden {new_order_data.id} emitida y guardada.")

def initialize_webcam():
    """Inicializa la webcam 0 con configuraciones o usa fallback de video."""
    global camera
    
    print(f"Intentando conectar a webcam {WEBCAM_ID}...")
    cap_attempts = [(WEBCAM_ID, cv2.CAP_DSHOW), (WEBCAM_ID, cv2.CAP_V4L2), (WEBCAM_ID, cv2.CAP_ANY)]
    camera_opened = False
    
    for current_id, backend in cap_attempts:
        camera = cv2.VideoCapture(current_id + backend)
        if camera.isOpened():
            camera.set(cv2.CAP_PROP_FRAME_WIDTH, BASE_FRAME_WIDTH)
            camera.set(cv2.CAP_PROP_FRAME_HEIGHT, BASE_FRAME_HEIGHT)
            camera.set(cv2.CAP_PROP_FPS, FPS)
            camera.set(cv2.CAP_PROP_BUFFERSIZE, 1)
            ret, test_frame = camera.read()
            if ret and test_frame is not None and np.sum(test_frame) > 1000:
                print(f"✓ Webcam {current_id}, Backend {backend} accedida correctamente.")
                camera_opened = True
                break
            else:
                camera.release()
                camera = None
    
    if not camera_opened:
        if os.path.exists(VIDEO_FALLBACK_PATH):
            camera = cv2.VideoCapture(VIDEO_FALLBACK_PATH)
            if not camera.isOpened():
                print(f"ERROR: No se pudo abrir el archivo de video: {VIDEO_FALLBACK_PATH}")
                return False
            else:
                camera.set(cv2.CAP_PROP_FRAME_WIDTH, BASE_FRAME_WIDTH)
                camera.set(cv2.CAP_PROP_FRAME_HEIGHT, BASE_FRAME_HEIGHT)
                camera_opened = True
        else:
            print(f"ERROR: No se pudo acceder a la webcam y el archivo '{VIDEO_FALLBACK_PATH}' no existe.")
            return False
    return camera_opened

def webcam_preview_thread():
    global camera, last_webcam_frame
    if not initialize_webcam():
        print("ERROR CRÍTICO: No se pudo inicializar ninguna fuente de video. Terminando hilo de preview.")
        return

    time.sleep(2)
    while True:
        try:
            with camera_lock:
                if camera is None or not camera.isOpened():
                    break
                ret, frame = camera.read()
            if not ret or frame is None:
                break
            
            h, w, _ = frame.shape
            target_w_for_h = int(h * TARGET_ASPECT_RATIO)
            frame_processed = frame[:, (w - target_w_for_h) // 2 : (w - target_w_for_h) // 2 + target_w_for_h] if w > target_w_for_h else frame
            if frame_processed.shape[1] != FINAL_OUTPUT_WIDTH or frame_processed.shape[0] != FINAL_OUTPUT_HEIGHT:
                frame_processed = cv2.resize(frame_processed, (FINAL_OUTPUT_WIDTH, FINAL_OUTPUT_HEIGHT))
            
            if np.sum(frame_processed) > 1000:
                with frame_buffer_lock:
                    last_webcam_frame = frame_processed.copy()
            
            cv2.putText(frame_processed, f'Cam ID {WEBCAM_ID}', (10, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)
            cv2.putText(frame_processed, f'Press S to Capture / Q to Exit', (10, frame_processed.shape[0] - 20), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)
            cv2.imshow('KDS Grill - Capture Station', frame_processed)
            key = cv2.waitKey(1) & 0xFF
            if key == ord('s') or key == ord('S'):
                threading.Thread(target=capture_and_send_order, daemon=True).start()
                time.sleep(0.5)
            elif key == ord('q') or key == ord('Q'):
                break
        except Exception as e:
            print(f"Error in preview loop: {e}")
            time.sleep(0.1)
            continue
    cleanup_camera()
    print("Webcam preview thread terminated.")

def cleanup_camera():
    global camera
    if camera is not None:
        with camera_lock:
            camera.release()
            camera = None
    cv2.destroyAllWindows()
    print("Camera resources released.")

def start_preview_thread_safe():
    global preview_thread_started
    with preview_thread_lock:
        if not preview_thread_started:
            preview_thread = threading.Thread(target=webcam_preview_thread, daemon=True)
            preview_thread.start()
            preview_thread_started = True
            return True
        return False

# --- Nuevo decorador para gestionar el contexto de la aplicación al inicio ---
@app.route('/')
def index():
    return "KDS Grill Backend - WebSockets Active"

@socketio.on('connect')
def test_connect(auth=None):
    print('Cliente conectado:', request.sid)
    # Al conectar, enviamos todas las órdenes persistentes
    with app.app_context():
        all_orders = Order.query.all()
        for order in all_orders:
            emit('new_order', order.to_dict())

@socketio.on('disconnect')
def test_disconnect():
    print('Cliente desconectado:', request.sid)

@socketio.on('update_order_status')
def handle_update_order_status(data):
    order_id = data.get('order_id')
    new_status = data.get('status')
    initial_duration = data.get('initial_duration')  # Nuevo parámetro
    
    with app.app_context():
        order_to_update = db.session.get(Order, order_id)
        if order_to_update:
            order_to_update.status = new_status
            if initial_duration is not None:
                order_to_update.initial_duration = initial_duration
            db.session.commit()
            print(f"Orden {order_id} actualizada a {new_status} en la DB.")
            # Emitir a todos los clientes que se actualizó la orden
            socketio.emit('order_updated', order_to_update.to_dict())

@socketio.on('remove_order')
def handle_remove_order(data):
    order_id = data.get('id')
    
    with app.app_context():
        order_to_remove = db.session.get(Order, order_id)
        if order_to_remove:
            db.session.delete(order_to_remove)
            db.session.commit()
            print(f"Orden {order_id} eliminada de la DB.")
            # Opcional: Emitir a todos los clientes que se eliminó la orden
            socketio.emit('order_removed', {'id': order_id})

# Bloque de ejecución principal
if __name__ == '__main__':
    print("=== KDS Grill - Sistema de Captura de Órdenes ===")
    print(f"Configurado para usar webcam {WEBCAM_ID}")
    
    # --- Cambio de sintaxis aquí ---
    with app.app_context():
        db.create_all()
    
    if os.environ.get('WERKZEUG_RUN_MAIN') != 'true':
        print("Proceso principal detectado - iniciando hilo de preview de forma segura.")
        start_preview_thread_safe()
    else:
        print("Proceso de reloader detectado - saltando inicio de hilo de preview.")
        
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
        print("\nInterrupción del usuario detectada...")
    except Exception as e:
        print(f"Error al iniciar el servidor Flask-SocketIO: {e}")
    finally:
        print("Cerrando aplicación...")
        cleanup_camera()
        print("Aplicación Flask-SocketIO terminada.")

# ======================
# ADMIN ENDPOINTS
# ======================

@app.route('/admin/orders/<order_id>/requeue', methods=['POST'])
@jwt_required()
def requeue_order(order_id):
    """Reingresa una orden al sistema"""
    order = Order.query.get(order_id)
    if not order:
        return jsonify({"error": "Orden no encontrada"}), 404

    order.status = "COOKING"
    order.startTime = datetime.now()
    order.initialDuration = 15 * 60  # 15 mins
    
    db.session.commit()
    
    socketio.emit('new_order', order.to_dict())  # Reenvía al KDS
    
    return jsonify({
        "message": f"Orden {order_id} reingresada",
        "order": order.to_dict()
    }), 200

@app.route('/admin/reports/daily', methods=['GET'])
@jwt_required()
def daily_report():
    """Genera reporte diario de órdenes"""
    today = datetime.now().date()
    
    orders = Order.query.filter(
        func.date(Order.created_at) == today
    ).all()
    
    status_counts = db.session.query(
        Order.status,
        func.count(Order.id)
    ).filter(
        func.date(Order.created_at) == today
    ).group_by(Order.status).all()
    
    avg_time = db.session.query(
        func.avg(Order.completed_at - Order.started_at)
    ).filter(
        Order.status == 'READY',
        func.date(Order.created_at) == today
    ).scalar()
    
    return jsonify({
        "date": today.isoformat(),
        "total_orders": len(orders),
        "status_counts": dict(status_counts),
        "avg_completion_time": str(avg_time) if avg_time else None,
        "orders": [o.to_dict() for o in orders]
    }), 200