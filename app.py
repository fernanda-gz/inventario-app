from flask import Flask, render_template, request, jsonify, redirect, url_for
import os
import psycopg2
import psycopg2.extras
import psycopg2.errors  # <-- Necesario para UniqueViolation
from urllib.parse import urlparse
from datetime import datetime, timedelta

app = Flask(__name__)

# ------------------------------------------------------------
# Conexión a la base de datos PostgreSQL
# ------------------------------------------------------------
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
        # Desarrollo local (opcional)
        conn = psycopg2.connect(
            database="inventario",
            user="postgres",
            password="admin",
            host="localhost",
            port="5432"
        )
    conn.cursor_factory = psycopg2.extras.RealDictCursor
    return conn

# ------------------------------------------------------------
# Función para calcular el stock mínimo sugerido
# (doble del total vendido en los últimos 7 días, mínimo 5)
# ------------------------------------------------------------
def calcular_minimo_por_sku(sku):
    if not sku:
        return 5
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT COALESCE(SUM(v.cantidad), 0) as total_vendido
        FROM ventas v
        JOIN piezas p ON v.pieza_id = p.id
        WHERE p.sku = %s AND v.fecha >= NOW() - INTERVAL '7 days'
    """, (sku,))
    result = cur.fetchone()
    conn.close()
    total = result['total_vendido'] if result else 0
    return max(total * 2, 5)

# ------------------------------------------------------------
# Ruta principal - Dashboard
# ------------------------------------------------------------
@app.route('/')
def dashboard():
    conn = get_db()
    proveedores = []
    try:
        cur = conn.cursor()
        cur.execute("SELECT id, nombre FROM proveedores WHERE activo = true ORDER BY nombre")
        proveedores = cur.fetchall()
    except:
        pass
    conn.close()
    return render_template('dashboard.html', proveedores=proveedores)

# ------------------------------------------------------------
# API: Datos para el dashboard (filtros flexibles)
# ------------------------------------------------------------
@app.route('/api/dashboard/datos')
def api_dashboard():
    fecha_inicio = request.args.get('fecha_inicio', (datetime.now() - timedelta(days=30)).strftime('%Y-%m-%d'))
    fecha_fin = request.args.get('fecha_fin', datetime.now().strftime('%Y-%m-%d'))
    proveedor_id = request.args.get('proveedor_id', None)
    agrupar_por = request.args.get('agrupar_por', 'dia')  # dia, mes, año

    if agrupar_por == 'mes':
        formato_fecha = "to_char(fecha, 'YYYY-MM')"
    elif agrupar_por == 'año':
        formato_fecha = "to_char(fecha, 'YYYY')"
    else:
        formato_fecha = "fecha::date"

    conn = get_db()
    cur = conn.cursor()

    # Ventas en el período
    query_ventas = f"""
        SELECT {formato_fecha} as periodo,
               COUNT(*) as num_ventas,
               COALESCE(SUM(total), 0) as total_ventas
        FROM ventas
        WHERE fecha BETWEEN %s AND %s
        GROUP BY periodo
        ORDER BY periodo
    """
    cur.execute(query_ventas, (fecha_inicio, fecha_fin))
    ventas = cur.fetchall()

    # Compras por proveedor
    query_compras = """
        SELECT p.nombre as proveedor,
               COUNT(c.id) as num_compras,
               COALESCE(SUM(c.total), 0) as total_compras
        FROM compras c
        JOIN proveedores p ON c.proveedor_id = p.id
        WHERE c.fecha BETWEEN %s AND %s
    """
    params = [fecha_inicio, fecha_fin]
    if proveedor_id:
        query_compras += " AND c.proveedor_id = %s"
        params.append(proveedor_id)
    query_compras += " GROUP BY p.nombre ORDER BY total_compras DESC"
    cur.execute(query_compras, params)
    compras = cur.fetchall()

    # Stock bajo
    cur.execute("""
        SELECT p.nombre_interno, p.sku, p.stock_actual, p.stock_minimo,
               STRING_AGG(ep.codigo_proveedor, ', ') as codigos
        FROM piezas p
        LEFT JOIN equivalencias_proveedor ep ON p.id = ep.pieza_id
        WHERE p.stock_actual <= p.stock_minimo AND p.activo = true
        GROUP BY p.id
        ORDER BY p.stock_actual ASC
    """)
    stock_bajo = cur.fetchall()

    # Top 10 piezas más vendidas
    cur.execute("""
        SELECT p.nombre_interno,
               SUM(v.cantidad) as total_vendido,
               SUM(v.total) as ingresos
        FROM ventas v
        JOIN piezas p ON v.pieza_id = p.id
        WHERE v.fecha BETWEEN %s AND %s
        GROUP BY p.id, p.nombre_interno
        ORDER BY total_vendido DESC
        LIMIT 10
    """, (fecha_inicio, fecha_fin))
    top_piezas = cur.fetchall()

    conn.close()

    return jsonify({
        'ventas': [dict(row) for row in ventas],
        'compras': [dict(row) for row in compras],
        'stock_bajo': [dict(row) for row in stock_bajo],
        'top_piezas': [dict(row) for row in top_piezas]
    })

# ------------------------------------------------------------
# Buscar pieza por código o nombre (para búsqueda rápida)
# ------------------------------------------------------------
@app.route('/api/buscar_pieza')
def buscar_pieza():
    q = request.args.get('q', '')
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT DISTINCT p.id, p.sku, p.nombre_interno, p.stock_actual,
               ep.codigo_proveedor, ep.nombre_proveedor, pr.nombre as proveedor
        FROM piezas p
        LEFT JOIN equivalencias_proveedor ep ON p.id = ep.pieza_id
        LEFT JOIN proveedores pr ON ep.proveedor_id = pr.id
        WHERE p.nombre_interno ILIKE %s
           OR p.sku ILIKE %s
           OR ep.codigo_proveedor ILIKE %s
           OR ep.nombre_proveedor ILIKE %s
        LIMIT 20
    """, (f'%{q}%', f'%{q}%', f'%{q}%', f'%{q}%'))
    resultados = cur.fetchall()
    conn.close()
    return jsonify([dict(row) for row in resultados])

# ------------------------------------------------------------
# Gestión de Proveedores
# ------------------------------------------------------------
@app.route('/proveedores')
def proveedores():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("SELECT * FROM proveedores ORDER BY nombre")
    proveedores = cur.fetchall()
    conn.close()
    return render_template('proveedores.html', proveedores=proveedores)

@app.route('/api/proveedores', methods=['POST'])
def agregar_proveedor():
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO proveedores (nombre, contacto, telefono, email)
        VALUES (%s, %s, %s, %s)
    """, (data['nombre'], data.get('contacto', ''), data.get('telefono', ''), data.get('email', '')))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

@app.route('/api/proveedores/<int:id>', methods=['PUT'])
def editar_proveedor(id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        UPDATE proveedores SET nombre=%s, contacto=%s, telefono=%s, email=%s, activo=%s
        WHERE id=%s
    """, (data.get('nombre', ''), data.get('contacto', ''), data.get('telefono', ''),
          data.get('email', ''), data.get('activo', True), id))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ------------------------------------------------------------
# Gestión de Piezas (con SKU y mínimo automático)
# ------------------------------------------------------------
@app.route('/piezas')
def piezas():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT p.id, p.sku, p.nombre_interno, p.descripcion, p.stock_actual, p.stock_minimo,
               p.ubicacion, p.activo, p.fecha_creacion,
               COALESCE(SUM(v.cantidad), 0) as total_vendido
        FROM piezas p
        LEFT JOIN ventas v ON p.id = v.pieza_id
        GROUP BY p.id
        ORDER BY p.nombre_interno
    """)
    piezas = cur.fetchall()
    conn.close()
    return render_template('piezas.html', piezas=piezas)

@app.route('/api/piezas', methods=['POST'])
def agregar_pieza():
    data = request.json
    sku = data.get('sku', '').strip()
    nombre = data['nombre_interno']
    stock_minimo = data.get('stock_minimo', 5)

    # Si no se envió stock_minimo o es 0, calcular automáticamente
    if not stock_minimo or int(stock_minimo) == 0:
        stock_minimo = calcular_minimo_por_sku(sku)

    conn = get_db()
    cur = conn.cursor()
    try:
        cur.execute("""
            INSERT INTO piezas (sku, nombre_interno, descripcion, stock_actual, stock_minimo, ubicacion)
            VALUES (%s, %s, %s, %s, %s, %s)
        """, (sku, nombre, data.get('descripcion', ''),
              data.get('stock_actual', 0), stock_minimo, data.get('ubicacion', '')))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.errors.UniqueViolation:
        conn.close()
        return jsonify({'error': 'El SKU ya existe. Use un código único.'}), 400

@app.route('/api/piezas/<int:id>', methods=['PUT'])
def editar_pieza(id):
    data = request.json
    conn = get_db()
    cur = conn.cursor()

    sku = data.get('sku', None)
    nombre = data.get('nombre_interno', '')
    stock_minimo = data.get('stock_minimo', None)

    # Si no se especifica mínimo o es 0, calcular
    if stock_minimo is None or int(stock_minimo) == 0:
        stock_minimo = calcular_minimo_por_sku(sku) if sku else 5

    try:
        cur.execute("""
            UPDATE piezas SET 
                nombre_interno = %s,
                sku = COALESCE(%s, sku),
                descripcion = %s,
                stock_actual = %s,
                stock_minimo = %s,
                ubicacion = %s,
                activo = %s
            WHERE id = %s
        """, (
            nombre,
            sku,
            data.get('descripcion', ''),
            data.get('stock_actual', 0),
            stock_minimo,
            data.get('ubicacion', ''),
            data.get('activo', True),
            id
        ))
        conn.commit()
        conn.close()
        return jsonify({'success': True})
    except psycopg2.errors.UniqueViolation:
        conn.close()
        return jsonify({'error': 'El SKU ya existe. Use un código único.'}), 400

@app.route('/api/stock-minimo-sugerido')
def stock_minimo_sugerido():
    sku = request.args.get('sku', '')
    sugerido = calcular_minimo_por_sku(sku)
    return jsonify({'sugerido': sugerido})

# ------------------------------------------------------------
# Gestión de Equivalencias (pieza - proveedor - código)
# ------------------------------------------------------------
@app.route('/equivalencias')
def equivalencias():
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        SELECT ep.id, p.nombre_interno, pr.nombre as proveedor,
               ep.codigo_proveedor, ep.nombre_proveedor, ep.precio_compra
        FROM equivalencias_proveedor ep
        JOIN piezas p ON ep.pieza_id = p.id
        JOIN proveedores pr ON ep.proveedor_id = pr.id
        ORDER BY p.nombre_interno, pr.nombre
    """)
    equivalencias = cur.fetchall()
    cur.execute("SELECT id, nombre_interno FROM piezas WHERE activo=true ORDER BY nombre_interno")
    piezas = cur.fetchall()
    cur.execute("SELECT id, nombre FROM proveedores WHERE activo=true ORDER BY nombre")
    proveedores = cur.fetchall()
    conn.close()
    return render_template('equivalencias.html', equivalencias=equivalencias, piezas=piezas, proveedores=proveedores)

@app.route('/api/equivalencias', methods=['POST'])
def agregar_equivalencia():
    data = request.json
    conn = get_db()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO equivalencias_proveedor (pieza_id, proveedor_id, codigo_proveedor, nombre_proveedor, precio_compra)
        VALUES (%s, %s, %s, %s, %s)
    """, (data['pieza_id'], data['proveedor_id'], data['codigo_proveedor'],
          data.get('nombre_proveedor', ''), data.get('precio_compra', 0)))
    conn.commit()
    conn.close()
    return jsonify({'success': True})

# ------------------------------------------------------------
# Registro de Compras
# ------------------------------------------------------------
@app.route('/compras', methods=['GET', 'POST'])
def compras():
    conn = get_db()
    if request.method == 'POST':
        data = request.form
        cur = conn.cursor()
        # Buscar la equivalencia o crear si no existe
        cur.execute("SELECT id FROM equivalencias_proveedor WHERE pieza_id=%s AND proveedor_id=%s",
                    (data['pieza_id'], data['proveedor_id']))
        eq = cur.fetchone()
        if not eq:
            cur.execute("""
                INSERT INTO equivalencias_proveedor (pieza_id, proveedor_id, codigo_proveedor, nombre_proveedor)
                VALUES (%s, %s, %s, %s) RETURNING id
            """, (data['pieza_id'], data['proveedor_id'], data.get('codigo_proveedor', ''), data.get('nombre_proveedor', '')))
            eq_id = cur.fetchone()['id']
        else:
            eq_id = eq['id']

        total = float(data['cantidad']) * float(data['precio_unitario'])
        cur.execute("""
            INSERT INTO compras (fecha, proveedor_id, equivalencia_id, cantidad, precio_unitario, total, numero_factura)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (data['fecha'], data['proveedor_id'], eq_id, data['cantidad'],
              data['precio_unitario'], total, data.get('numero_factura', '')))
        # Actualizar stock (sumar)
        cur.execute("UPDATE piezas SET stock_actual = stock_actual + %s WHERE id = %s",
                    (data['cantidad'], data['pieza_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('compras'))

    cur = conn.cursor()
    cur.execute("""
        SELECT c.id, c.fecha, p.nombre as proveedor, pi.nombre_interno, c.cantidad, c.precio_unitario, c.total
        FROM compras c
        JOIN proveedores p ON c.proveedor_id = p.id
        JOIN equivalencias_proveedor ep ON c.equivalencia_id = ep.id
        JOIN piezas pi ON ep.pieza_id = pi.id
        ORDER BY c.fecha DESC
        LIMIT 100
    """)
    lista_compras = cur.fetchall()
    cur.execute("SELECT id, nombre_interno FROM piezas WHERE activo=true ORDER BY nombre_interno")
    piezas = cur.fetchall()
    cur.execute("SELECT id, nombre FROM proveedores WHERE activo=true ORDER BY nombre")
    proveedores = cur.fetchall()
    conn.close()
    return render_template('compras.html', compras=lista_compras, piezas=piezas, proveedores=proveedores, hoy=datetime.now().strftime('%Y-%m-%d'))

# ------------------------------------------------------------
# Registro de Ventas
# ------------------------------------------------------------
@app.route('/ventas', methods=['GET', 'POST'])
def ventas():
    conn = get_db()
    if request.method == 'POST':
        data = request.form
        cur = conn.cursor()
        total = float(data['cantidad']) * float(data['precio_unitario'])
        cur.execute("""
            INSERT INTO ventas (fecha, pieza_id, cantidad, precio_unitario, total, cliente, numero_factura)
            VALUES (%s, %s, %s, %s, %s, %s, %s)
        """, (data['fecha'], data['pieza_id'], data['cantidad'],
              data['precio_unitario'], total, data.get('cliente', ''), data.get('numero_factura', '')))
        # Actualizar stock (restar)
        cur.execute("UPDATE piezas SET stock_actual = stock_actual - %s WHERE id = %s",
                    (data['cantidad'], data['pieza_id']))
        conn.commit()
        conn.close()
        return redirect(url_for('ventas'))

    cur = conn.cursor()
    cur.execute("""
        SELECT v.id, v.fecha, p.nombre_interno, v.cantidad, v.precio_unitario, v.total, v.cliente
        FROM ventas v
        JOIN piezas p ON v.pieza_id = p.id
        ORDER BY v.fecha DESC
        LIMIT 100
    """)
    lista_ventas = cur.fetchall()
    cur.execute("SELECT id, nombre_interno, precio_venta FROM piezas WHERE activo=true ORDER BY nombre_interno")
    piezas = cur.fetchall()
    conn.close()
    return render_template('ventas.html', ventas=lista_ventas, piezas=piezas)

# ------------------------------------------------------------
# Reportes
# ------------------------------------------------------------
@app.route('/reportes')
def reportes():
    return render_template('reportes.html')

# ------------------------------------------------------------
# Ruta para crear/actualizar tablas (agrega campo SKU si falta)
# ------------------------------------------------------------
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
            sku VARCHAR(100) UNIQUE,
            descripcion TEXT,
            stock_actual INTEGER DEFAULT 0,
            stock_minimo INTEGER DEFAULT 5,
            ubicacion VARCHAR(100),
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
            cliente VARCHAR(200),
            numero_factura VARCHAR(100)
        );
        CREATE TABLE IF NOT EXISTS compras (
            id SERIAL PRIMARY KEY,
            fecha TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            proveedor_id INTEGER REFERENCES proveedores(id),
            equivalencia_id INTEGER REFERENCES equivalencias_proveedor(id),
            cantidad INTEGER NOT NULL,
            precio_unitario DECIMAL(10,2) NOT NULL,
            total DECIMAL(10,2) NOT NULL,
            numero_factura VARCHAR(100)
        );
        CREATE INDEX IF NOT EXISTS idx_ventas_fecha ON ventas(fecha);
        CREATE INDEX IF NOT EXISTS idx_compras_fecha ON compras(fecha);
    """)

    # Agregar columna sku si no existe
    try:
        cur.execute("ALTER TABLE piezas ADD COLUMN sku VARCHAR(100) UNIQUE")
    except:
        pass  # ya existe, ignorar error

    conn.commit()
    conn.close()
    return "✅ Tablas actualizadas. Campo SKU agregado si faltaba."

if __name__ == '__main__':
    app.run(debug=True)