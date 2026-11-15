import os, time, hmac, hashlib, sqlite3, requests
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from dotenv import load_dotenv
from database import init_db, salvar_token, obter_token, DB_NAME

load_dotenv()
init_db()
app = FastAPI()

ML_APP_ID = os.getenv("ML_APP_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_REDIRECT_URI = os.getenv("ML_REDIRECT_URI", "")
SHOPEE_PARTNER_ID = os.getenv("SHOPEE_PARTNER_ID", "")
SHOPEE_PARTNER_KEY = os.getenv("SHOPEE_PARTNER_KEY", "")
SHOPEE_REDIRECT_URI = os.getenv("SHOPEE_REDIRECT_URI", "")

def get_valid_ml_token():
    conta = obter_token("mercadolivre")
    if not conta: return None
    if time.time() >= (conta["expires_at"] - 300):
        res = requests.post("https://mercadolibre.com", data={
            "grant_type": "refresh_token", "client_id": ML_APP_ID,
            "client_secret": ML_CLIENT_SECRET, "refresh_token": conta["refresh_token"]
        }).json()
        if "access_token" in res:
            salvar_token("mercadolivre", res["access_token"], res["refresh_token"], int(time.time()) + res["expires_in"])
            return res["access_token"]
    return conta["access_token"]

def gerar_assinatura_shopee(path, timestamp, access_token="", shop_id=""):
    base = f"{SHOPEE_PARTNER_ID}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(SHOPEE_PARTNER_KEY.encode(), base.encode(), hashlib.sha256).hexdigest()

@app.get("/conectar/ml")
def conectar_ml():
    return RedirectResponse(f"https://mercadolivre.com.br{ML_APP_ID}&redirect_uri={ML_REDIRECT_URI}")

@app.get("/callback/ml")
def callback_ml(code: str):
    res = requests.post("https://mercadolibre.com", data={
        "grant_type": "authorization_code", "client_id": ML_APP_ID,
        "client_secret": ML_CLIENT_SECRET, "code": code, "redirect_uri": ML_REDIRECT_URI
    }).json()
    if "access_token" in res:
        salvar_token("mercadolivre", res["access_token"], res["refresh_token"], int(time.time()) + res["expires_in"])
    return RedirectResponse("/")

@app.get("/conectar/shopee")
def conectar_shopee():
    path, ts = "/api/v2/shop/auth_partner", int(time.time())
    sign = gerar_assinatura_shopee(path, ts)
    return RedirectResponse(f"https://shopeemobile.com{path}?partner_id={SHOPEE_PARTNER_ID}&timestamp={ts}&sign={sign}&redirect={SHOPEE_REDIRECT_URI}")

@app.get("/callback/shopee")
def callback_shopee(code: str, shop_id: str):
    path, ts = "/api/v2/auth/token/get", int(time.time())
    sign = gerar_assinatura_shopee(path, ts)
    res = requests.post(f"https://shopeemobile.com{path}?partner_id={int(SHOPEE_PARTNER_ID)}&timestamp={ts}&sign={sign}",
        json={"code": code, "shop_id": int(shop_id), "partner_id": int(SHOPEE_PARTNER_ID)}).json()
    if "access_token" in res:
        salvar_token("shopee", res["access_token"], res["refresh_token"], int(time.time()) + res["expire_in"], shop_id)
    return RedirectResponse("/")

def calcular_preco(custo, margem, taxa):
    div = 1 - ((margem + taxa) / 100)
    return round(custo / div, 2) if div > 0 else round(custo * 2, 2)

@app.post("/produtos/adicionar")
def add_prod(sku_fornecedor: str = Form(...), nome: str = Form(...), id_anuncio_ml: str = Form(""),
             id_anuncio_shopee: str = Form(""), preco_custo: float = Form(...), margem_lucro: float = Form(20.0),
             taxa_marketplace: float = Form(18.0), estoque: int = Form(...)):
    conn = sqlite3.connect(DB_NAME)
    conn.execute("INSERT OR REPLACE INTO produtos (sku_fornecedor, nome, id_anuncio_ml, id_anuncio_shopee, preco_custo, margem_lucro, taxa_marketplace, estoque) VALUES (?,?,?,?,?,?,?,?)",
                 (sku_fornecedor, nome, id_anuncio_ml.strip(), id_anuncio_shopee.strip(), preco_custo, margem_lucro, taxa_marketplace, estoque))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/sincronizar/{pid}")
def sync_prod(pid: int):
    conn = sqlite3.connect(DB_NAME)
    p = conn.execute("SELECT id_anuncio_ml, preco_custo, margem_lucro, taxa_marketplace, estoque FROM produtos WHERE id=?", (pid,)).fetchone()
    conn.close()
    if p and p[0] and get_valid_ml_token():
        requests.put(f"https://mercadolibre.com{p[0]}",
            headers={"Authorization": f"Bearer {get_valid_ml_token()}"},
            json={"price": calcular_preco(p[1], p[2], p[3]), "available_quantity": p[4], "status": "paused" if p[4] <= 0 else "active"})
    return RedirectResponse("/", status_code=303)

@app.post("/webhook/pedidos")
async def webhook(request: Request):
    dados = await request.json()
    if dados.get("topic") == "orders_v2" and get_valid_ml_token():
        info = requests.get(f"https://mercadolibre.com{dados.get('resource')}", headers={"Authorization": f"Bearer {get_valid_ml_token()}"}).json()
        itens = info.get("order_items", [])
        sku = itens[0]["item"].get("seller_sku", "N/A") if itens else "N/A"
        conn = sqlite3.connect(DB_NAME)
        conn.execute("INSERT OR IGNORE INTO pedidos (plataforma, id_pedido, sku, valor, status, data_criacao) VALUES (?,?,?,?,?,?)",
                     ("Mercado Livre", str(info.get("id")), sku, info.get("total_amount", 0.0), "Pendente", datetime.now().strftime("%d/%m/%Y %H:%M")))
        conn.commit()
        conn.close()
    return {"status": "ok"}

@app.get("/", response_class=HTMLResponse)
def home():
    ml_ok, sh_ok = obter_token("mercadolivre") is not None, obter_token("shopee") is not None
    conn = sqlite3.connect(DB_NAME)
    prods = conn.execute("SELECT id, sku_fornecedor, nome, id_anuncio_ml, id_anuncio_shopee, preco_custo, margem_lucro, taxa_marketplace, estoque FROM produtos").fetchall()
    peds = conn.execute("SELECT plataforma, id_pedido, sku, valor, status, data_criacao FROM pedidos ORDER BY id DESC").fetchall()
    conn.close()
    lp = "".join([f"<tr class='border-b'><td class='p-2'>{x[1]}</td><td class='p-2'>{x[2]}</td><td class='p-2 text-xs'>ML:{x[3]}<br>SH:{x[4]}</td><td class='p-2'>R${x[5]:.2f}</td><td class='p-2 font-bold text-green-600'>R${calcular_preco(x[5],x[6],x[7]):.2f}</td><td class='p-2'>{x[8]}</td><td class='p-2'><form action='/sincronizar/{x[0]}' method='post'><button class='bg-blue-600 text-white px-2 py-1 rounded text-xs'>Sync</button></form></td></tr>" for x in prods])
    lped = "".join([f"<tr class='border-b'><td class='p-2'>{x[0]}</td><td class='p-2'>{x[1]}</td><td class='p-2'>{x[2]}</td><td class='p-2'>R${x[3]:.2f}</td><td class='p-2'>{x[4]}</td><td class='p-2 text-xs'>{x[5]}</td></tr>" for x in peds])
    return f"""<!DOCTYPE html><html><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width,initial-scale=1"><script src="https://tailwindcss.com"></script></head>
<body class="bg-gray-100 p-4"><div class="max-w-4xl mx-auto space-y-4">
<h1 class="text-xl font-bold">🚀 Meu Integrador (ML & Shopee)</h1>
<div class="bg-white p-4 rounded shadow flex gap-2"><a href="/conectar/ml" class="px-3 py-2 rounded text-sm font-bold {'bg-green-600 text-white' if ml_ok else 'bg-yellow-400'}">{'✅ ML OK' if ml_ok else 'Conectar ML'}</a>
<a href="/conectar/shopee" class="px-3 py-2 rounded text-sm font-bold {'bg-green-600 text-white' if sh_ok else 'bg-orange-500 text-white'}">{'✅ Shopee OK' if sh_ok else 'Conectar Shopee'}</a></div>
<div class="bg-white p-4 rounded shadow"><h2 class="font-bold mb-2">Cadastrar Produto</h2><form action="/produtos/adicionar" method="post" class="grid grid-cols-1 md:grid-cols-2 gap-2">
<input name="sku_fornecedor" placeholder="SKU Fornecedor" required class="border p-2 rounded"><input name="nome" placeholder="Nome Produto" required class="border p-2 rounded">
<input name="id_anuncio_ml" placeholder="ID ML (ex: MLB123)" class="border p-2 rounded"><input name="id_anuncio_shopee" placeholder="ID Shopee" class="border p-2 rounded">
<input type="number" step="0.01" name="preco_custo" placeholder="Custo R$" required class="border p-2 rounded"><input type="number" step="0.1" name="margem_lucro" value="20" class="border p-2 rounded">
<input type="number" step="0.1" name="taxa_marketplace" value="18" class="border p-2 rounded"><input type="number" name="estoque" placeholder="Estoque" required class="border p-2 rounded">
<button class="md:col-span-2 bg-slate-800 text-white py-2 rounded font-bold">Salvar Produto</button></form></div>
<div class="bg-white p-4 rounded shadow overflow-x-auto"><h2 class="font-bold mb-2">Produtos</h2><table class="w-full text-left text-sm"><thead><tr class="border-b"><th>SKU</th><th>Nome</th><th>IDs</th><th>Custo</th><th>Venda</th><th>Est.</th><th>Ação</th></tr></thead><tbody>{lp or '<tr><td colspan="7" class="p-3 text-gray-400">Vazio</td></tr>'}</tbody></table></div>
<div class="bg-white p-4 rounded shadow overflow-x-auto"><h2 class="font-bold mb-2">Pedidos</h2><table class="w-full text-left text-sm"><thead><tr class="border-b"><th>Canal</th><th>Pedido</th><th>SKU</th><th>Valor</th><th>Status</th><th>Data</th></tr></thead><tbody>{lped or '<tr><td colspan="6" class="p-3 text-gray-400">Sem pedidos</td></tr>'}</tbody></table></div>
</div></body></html>"""
