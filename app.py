from flask import Flask, render_template, jsonify, request
import os
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
from datetime import datetime, timedelta

app = Flask(__name__)

def get_db():
    database_url = os.environ.get('DATABASE_URL')
    if database_url:
        result = urlparse(database_url)
        conn = psycopg2.connect(
            database=result.path[1:],
            user=result.username,
            password=result.password,
            host=result.hostname,
            port=result.port
        )
    else:
        conn = psycopg2.connect(
            database="inventario",
            user="postgres",
            password="admin",
            host="localhost",
            port="5432"
        )
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn

@app.route('/')
def home():
    return "¡Sistema de Inventario Funcionando! 🚀"

@app.route('/crear-tablas')
def crear_tablas():
    conn = get_db()
    cur = conn.cursor()
    
    cur.execute("""
        CREATE TABLE IF NOT EXISTS proveedores (
            id SERIAL PRIMARY KEY,
            nombre VARCHAR(200) NOT NULL,
            contacto VARCHAR(200),
            telefono VARCHAR(50),
            email VARCHAR(100),
            activo BOOLEAN DEFAULT true,
            fecha_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS piezas (
            id SERIAL PRIMARY KEY,
            nombre_interno VARCHAR(200) NOT NULL,
            descripcion TEXT,
            stock_actual INTEGER DEFAULT 0,
            stock_minimo INTEGER DEFAULT 5,
            activo BOOLEAN DEFAULT true,
            fecha_creacion TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        
        CREATE TABLE IF NOT EXISTS equivalencias_proveedor (
            id SERIAL PRIMARY KEY,
            pieza_id INTEGER REFERENCES piezas(id),
            proveedor_id INTEGER REFERENCES proveedores(id),
            codigo_proveedor VARCHAR(100) NOT NULL,
            nombre_proveedor VARCHAR(200),
            precio_compra DECIMAL(10,2),
            activo BOOLEAN DEFAULT true
        );
        
        CREATE TABLE IF NOT EXISTS ventas (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            pieza_id INTEGER REFERENCES piezas(id),
            cantidad INTEGER NOT NULL,
            precio_unitario DECIMAL(10,2) NOT NULL,
            total DECIMAL(10,2) NOT NULL,
            cliente VARCHAR(200)
        );
        
        CREATE TABLE IF NOT EXISTS compras (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            proveedor_id INTEGER REFERENCES proveedores(id),
            pieza_id INTEGER REFERENCES piezas(id),
            cantidad INTEGER NOT NULL,
            precio_unitario DECIMAL(10,2) NOT NULL,
            total DECIMAL(10,2) NOT NULL
        );
    """)
    
    conn.commit()
    conn.close()
    return "✅ Tablas creadas exitosamente"

if __name__ == '__main__':
    app.run(debug=True)