import sqlite3

DB_NAME = "integrador.db"

def init_db():
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS contas (
        plataforma TEXT PRIMARY KEY,
        access_token TEXT,
        refresh_token TEXT,
        shop_id TEXT,
        expires_at INTEGER
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS produtos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        sku_fornecedor TEXT UNIQUE,
        nome TEXT,
        id_anuncio_ml TEXT,
        id_anuncio_shopee TEXT,
        preco_custo REAL,
        margem_lucro REAL,
        taxa_marketplace REAL,
        estoque INTEGER
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS pedidos (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        plataforma TEXT,
        id_pedido TEXT UNIQUE,
        sku TEXT,
        valor REAL,
        status TEXT,
        data_criacao TEXT
    )
    """)
    
    conn.commit()
    conn.close()

def salvar_token(plataforma, access_token, refresh_token, expires_at, shop_id=""):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO contas (plataforma, access_token, refresh_token, shop_id, expires_at)
    VALUES (?, ?, ?, ?, ?)
    """, (plataforma, access_token, refresh_token, shop_id, expires_at))
    conn.commit()
    conn.close()

def obter_token(plataforma):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT access_token, refresh_token, shop_id, expires_at FROM contas WHERE plataforma = ?", (plataforma,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return {"access_token": row[0], "refresh_token": row[1], "shop_id": row[2], "expires_at": row[3]}
    return None
