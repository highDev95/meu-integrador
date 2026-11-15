import os
import time
import hmac
import hashlib
import sqlite3
import requests
from datetime import datetime
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from dotenv import load_dotenv
from database import init_db, salvar_token, obter_token, DB_NAME

load_dotenv()
init_db()

app = FastAPI(title="Meu Integrador Dropshipping")

ML_APP_ID = os.getenv("ML_APP_ID", "")
ML_CLIENT_SECRET = os.getenv("ML_CLIENT_SECRET", "")
ML_REDIRECT_URI = os.getenv("ML_REDIRECT_URI", "")

SHOPEE_PARTNER_ID = os.getenv("SHOPEE_PARTNER_ID", "")
SHOPEE_PARTNER_KEY = os.getenv("SHOPEE_PARTNER_KEY", "")
SHOPEE_REDIRECT_URI = os.getenv("SHOPEE_REDIRECT_URI", "")


def get_valid_ml_token():
    conta = obter_token("mercadolivre")
    if not conta:
        return None
    if time.time() >= (conta["expires_at"] - 300):
        url = "https://mercadolibre.com"
        payload = {
            "grant_type": "refresh_token",
            "client_id": ML_APP_ID,
            "client_secret": ML_CLIENT_SECRET,
            "refresh_token": conta["refresh_token"]
        }
        res = requests.post(url, data=payload).json()
        if "access_token" in res:
            novo_expira = int(time.time()) + res["expires_in"]
            salvar_token("mercadolivre", res["access_token"], res["refresh_token"], novo_expira)
            return res["access_token"]
    return conta["access_token"]

def gerar_assinatura_shopee(path, timestamp, access_token="", shop_id=""):
    base_string = f"{SHOPEE_PARTNER_ID}{path}{timestamp}{access_token}{shop_id}"
    return hmac.new(
        SHOPEE_PARTNER_KEY.encode('utf-8'),
        base_string.encode('utf-8'),
        hashlib.sha256
    ).hexdigest()


@app.get("/conectar/ml")
def conectar_ml():
    url = f"https://mercadolivre.com.br{ML_APP_ID}&redirect_uri={ML_REDIRECT_URI}"
    return RedirectResponse(url)

@app.get("/callback/ml")
def callback_ml(code: str):
    url = "https://mercadolibre.com"
    payload = {
        "grant_type": "authorization_code",
        "client_id": ML_APP_ID,
        "client_secret": ML_CLIENT_SECRET,
        "code": code,
        "redirect_uri": ML_REDIRECT_URI
    }
    res = requests.post(url, data=payload).json()
    if "access_token" in res:
        expira_em = int(time.time()) + res["expires_in"]
        salvar_token("mercadolivre", res["access_token"], res["refresh_token"], expira_em)
    return RedirectResponse("/")

@app.get("/conectar/shopee")
def conectar_shopee():
    path = "/api/v2/shop/auth_partner"
    timestamp = int(time.time())
    sign = gerar_assinatura_shopee(path, timestamp)
    url = f"https://shopeemobile.com{path}?partner_id={SHOPEE_PARTNER_ID}&timestamp={timestamp}&sign={sign}&redirect={SHOPEE_REDIRECT_URI}"
    return RedirectResponse(url)

@app.get("/callback/shopee")
def callback_shopee(code: str, shop_id: str):
    path = "/api/v2/auth/token/get"
    timestamp = int(time.time())
    sign = gerar_assinatura_shopee(path, timestamp)
    url = f"https://shopeemobile.com{path}?partner_id={int(SHOPEE_PARTNER_ID)}&timestamp={timestamp}&sign={sign}"
    payload = {"code": code, "shop_id": int(shop_id), "partner_id": int(SHOPEE_PARTNER_ID)}
    res = requests.post(url, json=payload).json()
    if "access_token" in res:
        expira_em = int(time.time()) + res["expire_in"]
        salvar_token("shopee", res["access_token"], res["refresh_token"], expira_em, shop_id)
    return RedirectResponse("/")


def calcular_preco_venda(custo, margem_pct, taxa_mkt_pct):
    divisor = 1 - ((margem_pct + taxa_mkt_pct) / 100)
    if divisor <= 0:
        return custo * 2
    return round(custo / divisor, 2)

@app.post("/produtos/adicionar")
def adicionar_produto(
    sku_fornecedor: str = Form(...),
    nome: str = Form(...),
    id_anuncio_ml: str = Form(""),
    id_anuncio_shopee: str = Form(""),
    preco_custo: float = Form(...),
    margem_lucro: float = Form(20.0),
    taxa_marketplace: float = Form(18.0),
    estoque: int = Form(...)
):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("""
    INSERT OR REPLACE INTO produtos 
    (sku_fornecedor, nome, id_anuncio_ml, id_anuncio_shopee, preco_custo, margem_lucro, taxa_marketplace, estoque)
    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (sku_fornecedor, nome, id_anuncio_ml.strip(), id_anuncio_shopee.strip(), preco_custo, margem_lucro, taxa_marketplace, estoque))
    conn.commit()
    conn.close()
    return RedirectResponse("/", status_code=303)

@app.post("/sincronizar/{produto_id}")
def sincronizar_produto(produto_id: int):
    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT sku_fornecedor, id_anuncio_ml, id_anuncio_shopee, preco_custo, margem_lucro, taxa_marketplace, estoque FROM produtos WHERE id = ?", (produto_id,))
    prod = cursor.fetchone()
    conn.close()

    if not prod:
        return {"erro": "Produto não encontrado"}

    sku, id_ml, id_shopee, custo, margem, taxa, estoque = prod
    preco_final = calcular_preco_venda(custo, margem, taxa)

    ml_token = get_valid_ml_token()
    if id_ml and ml_token:
        url_ml = f"https://mercadolibre.com{id_ml}"
        headers = {"Authorization": f"Bearer {ml_token}", "Content-Type": "application/json"}
        payload = {"price": preco_final, "available_quantity": estoque}
        payload["status"] = "paused" if estoque <= 0 else "active"
        requests.put(url_ml, json=payload, headers=headers)

    return RedirectResponse("/", status_code=303)


@app.post("/webhook/pedidos")
async def receber_webhook(request: Request):
    dados = await request.json()
    
    if dados.get("topic") == "orders_v2":
        resource = dados.get("resource")
        ml_token = get_valid_ml_token()
        if ml_token and resource:
            pedido_info = requests.get(f"https://mercadolibre.com{resource}", headers={"Authorization": f"Bearer {ml_token}"}).json()
            id_pedido = str(pedido_info.get("id"))
            valor = pedido_info.get("total_amount", 0.0)
            itens = pedido_info.get("order_items", [])
            sku = itens[0]["item"].get("seller_sku", "N/A") if itens else "N/A"
            
            conn = sqlite3.connect(DB_NAME)
            cursor = conn.cursor()
            cursor.execute("""
            INSERT OR IGNORE INTO pedidos (plataforma, id_pedido, sku, valor, status, data_criacao)
            VALUES (?, ?, ?, ?, ?, ?)
            """, ("Mercado Livre", id_pedido, sku, valor, "Aguardando Fornecedor", datetime.now().strftime("%d/%m/%Y %H:%M")))
            conn.commit()
            conn.close()

    return {"status": "recebido"}


@app.get("/", response_class=HTMLResponse)
def dashboard():
    ml_ok = obter_token("mercadolivre") is not None
    shopee_ok = obter_token("shopee") is not None

    conn = sqlite3.connect(DB_NAME)
    cursor = conn.cursor()
    cursor.execute("SELECT id, sku_fornecedor, nome, id_anuncio_ml, id_anuncio_shopee, preco_custo, margem_lucro, taxa_marketplace, estoque FROM produtos")
    produtos = cursor.fetchall()
    cursor.execute("SELECT plataforma, id_pedido, sku, valor, status, data_criacao FROM pedidos ORDER BY id DESC")
    pedidos = cursor.fetchall()
    conn.close()

    linhas_produtos = ""
    for p in produtos:
        preco_venda = calcular_preco_venda(p[5], p[6], p[7])
        linhas_produtos += f"""
        <tr class="border-b">
            <td class="p-3 font-mono text-sm">{p[1]}</td>
            <td class="p-3">{p[2]}</td>
            <td class="p-3 text-xs text-gray-600">ML: {p[3] or '-'}<br>Shopee: {p[4] or '-'}</td>
            <td class="p-3">R$ {p[5]:.2f}</td>
            <td class="p-3 font-bold text-green-600">R$ {preco_venda:.2f}</td>
            <td class="p-3">{p[8]} un.</td>
            <td class="p-3">
                <form action="/sincronizar/{p[0]}" method="post">
                    <button class="bg-blue-600 text-white px-3 py-1 rounded text-xs hover:bg-blue-700">Sincronizar</button>
                </form>
            </td>
        </tr>
        """

    linhas_pedidos = ""
    for ped in pedidos:
        linhas_pedidos += f"""
        <tr class="border-b">
            <td class="p-3">{ped[0]}</td>
            <td class="p-3 font-mono">{ped[1]}</td>
            <td class="p-3">{ped[2]}</td>
            <td class="p-3">R$ {ped[3]:.2f}</td>
            <td class="p-3"><span class="bg-yellow-100 text-yellow-800 px-2 py-1 rounded text-xs">{ped[4]}</span></td>
            <td class="p-3 text-xs text-gray-500">{ped[5]}</td>
        </tr>
        """

    return f"""
    <!DOCTYPE html>
    <html lang="pt-BR">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Integrador Dropshipping Gratuito</title>
        <script src="https://tailwindcss.com"></script>
    </head>
    <body class="bg-gray-100 text-gray-800 p-4">
        <div class="max-w-6xl mx-auto space-y-6">
            <h1 class="text-xl font-bold">🚀 Meu Integrador Dropshipping (ML & Shopee)</h1>

            <div class="bg-white p-4 rounded shadow flex flex-col sm:flex-row gap-3 items-start sm:items-center">
                <div class="flex-1">
                    <h2 class="font-semibold">1. Conexões com Marketplaces</h2>
                    <p class="text-sm text-gray-500">Conecte suas contas de vendedor via API oficial.</p>
                </div>
                <div class="flex gap-2">
                    <a href="/conectar/ml" class="px-3 py-2 rounded text-sm font-medium {'bg-green-600 text-white' if ml_ok else 'bg-yellow-400 text-black'}">
                        {'✅ ML Conectado' if ml_ok else 'Conectar ML'}
                    </a>
