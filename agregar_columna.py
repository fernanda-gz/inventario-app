import psycopg2
from urllib.parse import urlparse

# Pega aquí tu External Database URL de Render
DATABASE_URL = input("Pega la External Database URL: ").strip()

result = urlparse(DATABASE_URL)
conn = psycopg2.connect(
    database=result.path[1:],
    user=result.username,
    password=result.password,
    host=result.hostname,
    port=result.port
)
cur = conn.cursor()

try:
    cur.execute("ALTER TABLE compras ADD COLUMN IF NOT EXISTS numero_factura VARCHAR(100)")
    conn.commit()
    print("✅ Columna 'numero_factura' agregada exitosamente.")
except Exception as e:
    print("❌ Error:", e)

cur.close()
conn.close()
