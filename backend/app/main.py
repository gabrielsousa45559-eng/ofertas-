from __future__ import annotations

import os
import base64
import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.cron import CronTrigger
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel, HttpUrl
from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Integer, String, Text, create_engine, func, inspect, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data/affiliate.db")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql+psycopg://", 1)
elif DATABASE_URL.startswith("postgresql://"):
    DATABASE_URL = DATABASE_URL.replace("postgresql://", "postgresql+psycopg://", 1)
IS_SQLITE = DATABASE_URL.startswith("sqlite")
if IS_SQLITE:
    os.makedirs("./data", exist_ok=True)
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if IS_SQLITE else {})
Session = sessionmaker(bind=engine, expire_on_commit=False)

class Base(DeclarativeBase): pass

class Offer(Base):
    __tablename__ = "offers"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    category: Mapped[str] = mapped_column(String(80), default="Geral")
    image_url: Mapped[str] = mapped_column(Text, default="")
    product_url: Mapped[str] = mapped_column(Text)
    affiliate_url: Mapped[str] = mapped_column(Text)
    price: Mapped[float] = mapped_column(Float, default=0)
    original_price: Mapped[float] = mapped_column(Float, default=0)
    commission: Mapped[float] = mapped_column(Float, default=0)
    copy: Mapped[str] = mapped_column(Text, default="")
    marketplace: Mapped[str] = mapped_column(String(30), default="mercadolivre")
    status: Mapped[str] = mapped_column(String(30), default="pending")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    clicks: Mapped[list["Click"]] = relationship(back_populates="offer", cascade="all, delete-orphan")

class Click(Base):
    __tablename__ = "clicks"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    offer_id: Mapped[int] = mapped_column(ForeignKey("offers.id"))
    source: Mapped[str] = mapped_column(String(120), default="direct")
    clicked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))
    offer: Mapped[Offer] = relationship(back_populates="clicks")

class Setting(Base):
    __tablename__ = "settings"
    key: Mapped[str] = mapped_column(String(100), primary_key=True)
    value: Mapped[str] = mapped_column(Text, default="")

class Schedule(Base):
    __tablename__ = "schedules"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    time: Mapped[str] = mapped_column(String(5))
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)

Base.metadata.create_all(engine)
# Upgrade databases created by earlier versions without discarding saved offers.
with engine.begin() as connection:
    if IS_SQLITE:
        columns = {row[1] for row in connection.exec_driver_sql("PRAGMA table_info(offers)").fetchall()}
    else:
        columns = {column["name"] for column in inspect(connection).get_columns("offers")}
    if "marketplace" not in columns: connection.exec_driver_sql("ALTER TABLE offers ADD COLUMN marketplace VARCHAR(30) DEFAULT 'mercadolivre'")
    if "status" not in columns: connection.exec_driver_sql("ALTER TABLE offers ADD COLUMN status VARCHAR(30) DEFAULT 'pending'")
app = FastAPI(title="Oferta Central API", version="1.0.0")
app.add_middleware(CORSMiddleware, allow_origins=os.getenv("CORS_ORIGINS", "http://localhost:5173").split(","), allow_methods=["*"], allow_headers=["*"])
scheduler = AsyncIOScheduler(timezone=os.getenv("TZ", "America/Sao_Paulo"))
DEFAULT_DISCOVERY_TERMS = "Eletrônicos: fone bluetooth\nEletrônicos: smartphone\nCasa e Cozinha: air fryer\nCasa e Cozinha: cafeteira\nModa: tênis\nBeleza: secador de cabelo"
DEFAULT_COPY_PROMPT = """Escreva em português brasileiro uma mensagem de Telegram com exatamente 3 linhas: (1) gancho de curiosidade/identificação com o título; (2) preço informado e, somente se houver preço anterior maior, desconto calculado; (3) convite leve para conferir os detalhes e decidir. Use até 3 emojis. Use apenas os dados fornecidos. Não invente recursos, avaliações, resultados, estoque limitado, prazo ou urgência. Não use frases de pressão como 'última chance', 'corra' ou 'não perca'. Não inclua URL nem aspas. Máximo 300 caracteres."""

def safe_promotional_copy(title: str, price: float, original_price: float = 0) -> str:
    current=f"{price:.2f}".replace(".",",")
    opening=f"👀 Procurando {title}? Vale conferir esta oferta."
    if original_price > price > 0:
        old=f"{original_price:.2f}".replace(".",",")
        discount=round((original_price-price)/original_price*100)
        pricing=f"💰 De R$ {old} por R$ {current} ({discount}% a menos)."
    else:
        pricing=f"💰 Preço informado: R$ {current}."
    return f"{opening}\n{pricing}\nConfira os detalhes e veja se combina com você 👇"

def clean_ai_copy(text: str, title: str, price: float, original_price: float = 0) -> str:
    cleaned=text.strip().strip('"“”\'')
    lower=cleaned.casefold()
    pressure_phrases=("última chance","ultima chance","corra","não perca","nao perca","não vai perder","nao vai perder","últimas unidades","ultimas unidades","estoque acabando","vai esgotar")
    if not cleaned or len(cleaned)>450 or any(phrase in lower for phrase in pressure_phrases):
        return safe_promotional_copy(title,price,original_price)
    return cleaned

class OfferIn(BaseModel):
    title: str
    category: str = "Geral"
    image_url: str = ""
    product_url: HttpUrl
    affiliate_url: HttpUrl | None = None
    price: float = 0
    original_price: float = 0
    commission: float = 0
    copy: str = ""
    marketplace: str = "mercadolivre"

class SettingIn(BaseModel): value: str
class ScheduleIn(BaseModel): time: str; enabled: bool = True
class AffiliateConvertIn(BaseModel): product_url: HttpUrl; title: str = "Link Mercado Livre"

def setting(db, key, default=""):
    env_key = {"ml_client_id": "ML_CLIENT_ID", "ml_client_secret": "ML_CLIENT_SECRET"}.get(key)
    if env_key and os.getenv(env_key):
        return os.getenv(env_key, "")
    row = db.get(Setting, key)
    return row.value if row else default

def build_affiliate_url(product_url: str, affiliate_tag: str) -> str:
    """Add the configured Mercado Livre tracking parameter without damaging the product URL."""
    parsed = urlparse(product_url.strip())
    host = (parsed.hostname or "").lower()
    valid_marketplace = host in {"mercadolivre.com.br", "www.mercadolivre.com.br", "mercadolivre.com", "www.mercadolivre.com", "mercadolibre.com", "www.mercadolibre.com"} or host.endswith(".mercadolivre.com.br") or host.endswith(".mercadolibre.com")
    if parsed.scheme != "https" or not valid_marketplace:
        raise HTTPException(400, "Use uma URL HTTPS de produto do Mercado Livre.")
    tag = affiliate_tag.strip()
    if not tag:
        raise HTTPException(400, "Configure o código matt_tool verdadeiro ou cole o link gerado no portal de afiliados.")
    if "/social/" in tag or tag.startswith("http://") or tag.startswith("https://"):
        raise HTTPException(400, "O link /social/ é do perfil, não é o valor do matt_tool. Use o link gerado no portal ou informe o valor do parâmetro de rastreio.")
    if tag.startswith("matt_tool="):
        tag = tag.split("=", 1)[1]
    if not tag or any(ch.isspace() for ch in tag):
        raise HTTPException(400, "O código de rastreio matt_tool está vazio ou inválido.")
    query = parse_qsl(parsed.query, keep_blank_values=True)
    if any(key == "matt_tool" for key, _ in query):
        return product_url
    query.append(("matt_tool", tag))
    return urlunparse(parsed._replace(query=urlencode(query, doseq=True)))

def tracking_url(offer_id: int, source: str = "direct") -> str:
    with Session() as db:
        base = setting(db, "public_base_url", os.getenv("PUBLIC_BASE_URL", "http://localhost:8000")).rstrip("/")
    return f"{base}/r/{offer_id}?source={source}"

def serialize(o: Offer):
    return {"id": o.id, "title": o.title, "category": o.category, "image_url": o.image_url, "product_url": o.product_url,
            "affiliate_url": o.affiliate_url, "price": o.price, "original_price": o.original_price, "commission": o.commission,
            "copy": o.copy, "marketplace": o.marketplace, "status": o.status,
            "tracking_url": tracking_url(o.id),
            "created_at": o.created_at.isoformat(), "clicks": len(o.clicks)}

async def ml_access_token(db):
    token = setting(db, "ml_access_token")
    expiry = float(setting(db, "ml_token_expires", "0") or 0)
    if token and expiry > datetime.now(timezone.utc).timestamp() + 90:
        return token
    refresh = setting(db, "ml_refresh_token")
    if not refresh: raise HTTPException(401, "Conecte sua conta do Mercado Livre em Configurações antes de buscar.")
    client_id, secret = setting(db, "ml_client_id"), setting(db, "ml_client_secret")
    async with httpx.AsyncClient(timeout=20) as client:
        response = await client.post("https://api.mercadolibre.com/oauth/token", data={"grant_type":"refresh_token","client_id":client_id,"client_secret":secret,"refresh_token":refresh})
    if response.is_error: raise HTTPException(401, "A autorização do Mercado Livre expirou. Conecte a conta novamente.")
    data = response.json()
    for key, value in {"ml_access_token":data["access_token"],"ml_refresh_token":data.get("refresh_token",refresh),"ml_token_expires":str(datetime.now(timezone.utc).timestamp()+data.get("expires_in",21600))}.items():
        row=db.get(Setting,key) or Setting(key=key); row.value=value; db.add(row)
    db.commit()
    return data["access_token"]

async def discover_marketplace(query: str, limit: int = 8, category: str = "Geral"):
    """Search the official product catalog, then resolve each result to a purchasable listing."""
    with Session() as db: token=await ml_access_token(db)
    async with httpx.AsyncClient(timeout=25) as client:
        headers={"Authorization":f"Bearer {token}"}
        response = await client.get("https://api.mercadolibre.com/products/search", headers=headers, params={"site_id":"MLB", "status":"active", "q":query, "limit":min(limit, 50), "offset":0})
        if response.is_error: raise HTTPException(502, f"Busca de catálogo Mercado Livre falhou ({response.status_code}).")
        catalog = response.json().get("results", [])
        candidates=[]
        for product in catalog:
            product_id=product.get("id")
            if not product_id: continue
            offers_response=await client.get(f"https://api.mercadolibre.com/products/{product_id}/items", headers=headers)
            if offers_response.is_error: continue
            offers_payload=offers_response.json()
            offers=offers_payload if isinstance(offers_payload,list) else offers_payload.get("results", offers_payload.get("items", []))
            if not offers: continue
            valid_offers=[offer for offer in offers if offer.get("item_id") and str(offer.get("currency_id") or "BRL")=="BRL" and offer.get("price") is not None]
            valid_offers=sorted(valid_offers,key=lambda offer: float(offer["price"]))
            for listing in valid_offers:
                item_id=listing["item_id"]
                item_response=await client.get(f"https://api.mercadolibre.com/items/{item_id}", headers=headers)
                if item_response.is_error: continue
                item=item_response.json()
                url=item.get("permalink")
                if not url: continue
                # Preserve offer identity and price from /products/{id}/items; do not
                # substitute catalog-page prices or a different seller's listing.
                candidates.append({"title":product.get("name") or item.get("title") or "Produto Mercado Livre",
                    "thumbnail":(product.get("pictures") or [{}])[0].get("url") or item.get("thumbnail") or "",
                    "permalink":url,"price":listing.get("price"),"original_price":listing.get("original_price")})
                break
    added, found = [], []
    with Session() as db:
        existing = {o.product_url for o in db.scalars(select(Offer)).all()}
        for item in candidates:
            url = item.get("permalink")
            if not url: continue
            found.append(item)
            if url in existing: continue
            tag = setting(db, "ml_affiliate_tag")
            try: affiliate_url = build_affiliate_url(url, tag) if tag else ""
            except HTTPException: affiliate_url = ""
            obj = Offer(title=item.get("title", "Produto Mercado Livre"), category=category,
                        image_url=item.get("thumbnail", ""), product_url=url, affiliate_url=affiliate_url,
                        price=float(item.get("price") or 0), original_price=float(item.get("original_price") or item.get("price") or 0),
                        commission=0, copy="", marketplace="mercadolivre", status="pending")
            db.add(obj); db.flush(); added.append(serialize(obj)); existing.add(url)
        db.commit()
    # Create a ready-to-edit promotional draft. User still approves the item and affiliate URL.
    with Session() as db:
        for item in added:
            offer=db.get(Offer,item["id"])
            if offer:
                prompt=setting(db,"ai_prompt",DEFAULT_COPY_PROMPT) or DEFAULT_COPY_PROMPT
                old_price=f"Preço anterior: R$ {offer.original_price:.2f}; redução calculada de {round((offer.original_price-offer.price)/offer.original_price*100)}%." if offer.original_price > offer.price > 0 else ""
                text=f"{prompt}\nProduto: {offer.title}\nCategoria: {offer.category}\nPreço atual: R$ {offer.price:.2f}. {old_price}\nResponda só com a mensagem promocional."
                base=setting(db,"ollama_url",os.getenv("OLLAMA_URL","http://localhost:11434"))
                try:
                    async with httpx.AsyncClient(timeout=180) as client:
                        generated=await client.post(base.rstrip("/")+"/api/generate",json={"model":"llama3.2:1b","prompt":text,"stream":False,"options":{"num_predict":72,"temperature":0.75}})
                    if generated.is_success: offer.copy=clean_ai_copy(generated.json().get("response",""),offer.title,offer.price,offer.original_price)
                except httpx.HTTPError: pass
                if not offer.copy: offer.copy=safe_promotional_copy(offer.title,offer.price,offer.original_price)
        db.commit()
    return {"query": query, "found": len(candidates), "added": len(added), "offers": added}

async def scheduled_discovery():
    with Session() as db:
        terms = setting(db, "discovery_terms", "")
        cursor = int(setting(db, "discovery_cursor", "0") or 0)
        if not setting(db,"ml_refresh_token") and not setting(db,"ml_access_token"): return
    parsed=[]
    for line in [part.strip() for part in terms.splitlines() if part.strip()]:
        category, sep, query = line.partition(":")
        parsed.append((category.strip() if sep else "Geral", query.strip() if sep else category.strip()))
    if not parsed: return
    count=min(3,len(parsed)); selected=[parsed[(cursor+i)%len(parsed)] for i in range(count)]
    for category, query in selected:
        try: await discover_marketplace(query, 2, category)
        except Exception as exc: print(f"Discovery error for {query}: {exc}")
    with Session() as db:
        row=db.get(Setting,"discovery_cursor") or Setting(key="discovery_cursor"); row.value=str((cursor+count)%len(parsed)); db.add(row); db.commit()

async def telegram_send(db, offer: Offer):
    if offer.status != "approved": raise HTTPException(400, "Aprove a oferta e informe seu link afiliado antes de publicar.")
    token, chat = setting(db, "telegram_token"), setting(db, "telegram_chat_id")
    if not token or not chat: raise HTTPException(400, "Configure o token e o canal do Telegram em Configurações.")
    base = setting(db, "public_base_url", "http://localhost:8000").rstrip("/")
    text = f"{offer.copy or offer.title}\n\n🔗 {base}/r/{offer.id}?source=telegram\nℹ️ Link de afiliado."
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(f"https://api.telegram.org/bot{token}/sendMessage", json={"chat_id": chat, "text": text, "disable_web_page_preview": False})
    if resp.is_error: raise HTTPException(resp.status_code, f"Telegram retornou erro: {resp.text[:300]}")

async def scheduled_post():
    with Session() as db:
        if setting(db, "telegram_auto", "false") != "true": return
        offer = db.scalar(select(Offer).where(Offer.status == "approved").order_by(Offer.created_at.asc()).limit(1))
        if offer:
            await telegram_send(db, offer)
            offer.status = "posted"; db.commit()

def refresh_jobs():
    for job in scheduler.get_jobs():
        if job.id.startswith("schedule-"): scheduler.remove_job(job.id)
    with Session() as db:
        for item in db.scalars(select(Schedule).where(Schedule.enabled)):
            hour, minute = map(int, item.time.split(":"))
            scheduler.add_job(scheduled_post, CronTrigger(hour=hour, minute=minute), id=f"schedule-{item.id}", replace_existing=True)

@app.on_event("startup")
async def startup():
    if not scheduler.running: scheduler.start()
    with Session() as db:
        existing_times=sorted(item.time for item in db.scalars(select(Schedule)).all())
        if not existing_times:
            db.add_all([Schedule(time=t) for t in ["09:00", "11:24", "13:48", "16:12", "18:36", "21:00"]])
        elif existing_times == sorted(["09:00", "12:00", "15:00", "18:00", "21:00"]):
            db.query(Schedule).delete()
            db.add_all([Schedule(time=t) for t in ["09:00", "11:24", "13:48", "16:12", "18:36", "21:00"]])
        if not setting(db, "discovery_terms"): db.add(Setting(key="discovery_terms", value=DEFAULT_DISCOVERY_TERMS))
        if not setting(db, "ai_prompt"): db.add(Setting(key="ai_prompt", value=DEFAULT_COPY_PROMPT))
        db.commit()
    refresh_jobs()
    scheduler.add_job(scheduled_discovery, IntervalTrigger(minutes=60), id="marketplace-discovery", replace_existing=True, next_run_time=datetime.now(timezone.utc))

@app.on_event("shutdown")
async def shutdown():
    if scheduler.running: scheduler.shutdown(wait=False)

@app.get("/api/health")
def health(): return {"status": "ok", "time": datetime.now(timezone.utc).isoformat()}

@app.get("/")
def dashboard():
    if os.path.exists("./static/index.html"):
        return FileResponse("./static/index.html")
    return {"message": "Frontend em desenvolvimento. Execute o Vite em http://localhost:5173"}

if os.path.isdir("./static/assets"):
    app.mount("/assets", StaticFiles(directory="./static/assets"), name="assets")

@app.get("/api/offers")
def list_offers():
    with Session() as db: return [serialize(o) for o in db.scalars(select(Offer).order_by(Offer.created_at.desc())).all()]

@app.post("/api/offers")
def create_offer(payload: OfferIn, request: Request):
    with Session() as db:
        aff = str(payload.affiliate_url or "")
        tag = setting(db, "ml_affiliate_tag")
        # A pre-generated official link is kept intact; otherwise apply only the configured tracking tag.
        aff = str(payload.affiliate_url) if payload.affiliate_url else (build_affiliate_url(str(payload.product_url), tag) if tag else "")
        obj = Offer(**payload.model_dump(exclude={"affiliate_url", "product_url"}), product_url=str(payload.product_url), affiliate_url=aff)
        db.add(obj); db.commit(); db.refresh(obj); return serialize(obj)

@app.post("/api/affiliate/convert")
def convert_affiliate_link(payload: AffiliateConvertIn):
    product_url = str(payload.product_url)
    with Session() as db:
        tag = setting(db, "ml_affiliate_tag")
        final_url = build_affiliate_url(product_url, tag)
        obj = Offer(title=payload.title, product_url=product_url, affiliate_url=final_url, status="pending", marketplace="mercadolivre")
        db.add(obj); db.commit(); db.refresh(obj)
        return {"id": obj.id, "affiliate_url": obj.affiliate_url, "tracking_url": tracking_url(obj.id), "status": obj.status}

@app.post("/api/discovery/search")
async def discovery_search(payload: dict):
    query = str(payload.get("query", "")).strip()
    if not query: raise HTTPException(400, "Informe o que deseja procurar.")
    return await discover_marketplace(query, category=str(payload.get("category", "Geral")))

@app.post("/api/discovery/run")
async def discovery_run():
    return await scheduled_discovery() or {"ok": True}

@app.put("/api/offers/{offer_id}/review")
def review_offer(offer_id: int, payload: dict):
    action = payload.get("action")
    if action not in {"approved", "rejected", "pending"}: raise HTTPException(400, "Ação inválida")
    with Session() as db:
        offer = db.get(Offer, offer_id)
        if not offer: raise HTTPException(404, "Oferta não encontrada")
        affiliate_url = str(payload.get("affiliate_url", "")).strip()
        if affiliate_url:
            if not affiliate_url.startswith("https://"): raise HTTPException(400, "O link de afiliado precisa começar com https://")
            affiliate_host=(urlparse(affiliate_url).hostname or "").lower()
            if affiliate_host not in {"mercadolivre.com.br","www.mercadolivre.com.br","mercadolivre.com","www.mercadolivre.com","mercadolibre.com","www.mercadolibre.com","meli.la"} and not affiliate_host.endswith(".mercadolivre.com.br") and not affiliate_host.endswith(".mercadolibre.com"):
                raise HTTPException(400, "Cole um link oficial do Mercado Livre ou meli.la.")
            offer.affiliate_url = affiliate_url
        elif action == "approved" and not offer.affiliate_url:
            tag = setting(db, "ml_affiliate_tag")
            if tag: offer.affiliate_url = build_affiliate_url(offer.product_url, tag)
        if action == "approved" and not offer.affiliate_url:
            raise HTTPException(400, "Configure um código matt_tool válido ou cole o link oficial gerado no portal de afiliados.")
        offer.status = action
        db.commit(); db.refresh(offer); return serialize(offer)

@app.put("/api/offers/{offer_id}/copy")
def update_copy(offer_id: int, payload: dict):
    with Session() as db:
        offer = db.get(Offer, offer_id)
        if not offer: raise HTTPException(404, "Oferta não encontrada")
        offer.copy = str(payload.get("copy", ""))[:5000]
        db.commit(); db.refresh(offer); return serialize(offer)

@app.delete("/api/offers/{offer_id}")
def delete_offer(offer_id: int):
    with Session() as db:
        obj = db.get(Offer, offer_id)
        if not obj: raise HTTPException(404, "Oferta não encontrada")
        db.delete(obj); db.commit(); return {"ok": True}

@app.get("/r/{offer_id}")
def track(offer_id: int, request: Request, source: str = "direct"):
    with Session() as db:
        offer = db.get(Offer, offer_id)
        if not offer: raise HTTPException(404, "Link não encontrado")
        if not offer.affiliate_url: raise HTTPException(409, "Esta oferta ainda não tem link afiliado configurado.")
        db.add(Click(offer_id=offer_id, source=source[:120])); db.commit()
        return RedirectResponse(offer.affiliate_url, status_code=302)

@app.get("/api/settings")
def get_settings():
    with Session() as db:
        keys = ["telegram_token", "telegram_chat_id", "telegram_group_url", "ai_provider", "ai_api_key", "ollama_url", "ml_affiliate_tag", "telegram_auto", "ai_prompt", "public_base_url", "discovery_terms", "ml_client_id", "ml_client_secret", "ml_connected"]
        result={k: setting(db, k, "false" if k == "telegram_auto" else "") for k in keys}
        # Never return credentials or OAuth tokens from the public settings API.
        for key in ("telegram_token", "ai_api_key", "ml_client_secret"):
            result[key] = ""
        result["telegram_token_configured"] = bool(setting(db, "telegram_token"))
        result["ai_api_key_configured"] = bool(setting(db, "ai_api_key"))
        result["ml_client_secret_configured"] = bool(setting(db, "ml_client_secret"))
        result["database_type"] = "SQLite" if IS_SQLITE else "PostgreSQL"
        result["ai_prompt"] = result["ai_prompt"] or DEFAULT_COPY_PROMPT
        result["discovery_terms"] = result["discovery_terms"] or DEFAULT_DISCOVERY_TERMS
        return result

@app.put("/api/settings/{key}")
def put_setting(key: str, payload: SettingIn):
    allowed = {"telegram_token", "telegram_chat_id", "telegram_group_url", "ai_provider", "ai_api_key", "ollama_url", "ml_affiliate_tag", "telegram_auto", "ai_prompt", "public_base_url", "discovery_terms", "ml_client_id", "ml_client_secret"}
    if key not in allowed: raise HTTPException(400, "Configuração inválida")
    with Session() as db:
        row = db.get(Setting, key)
        if key in {"telegram_token", "ai_api_key", "ml_client_secret"} and not payload.value and row:
            return {"key": key, "value": "", "configured": bool(row.value)}
        row = row or Setting(key=key); row.value = payload.value; db.add(row); db.commit()
    return {"key": key, "value": "" if key in {"telegram_token", "ai_api_key", "ml_client_secret"} else payload.value, "configured": bool(payload.value) if key in {"telegram_token", "ai_api_key", "ml_client_secret"} else None}

@app.get("/api/marketplace/mercadolivre/authorize")
def ml_authorize(request: Request):
    with Session() as db: client_id=setting(db,"ml_client_id")
    if not client_id: raise HTTPException(400,"Salve o Client ID do aplicativo Mercado Livre primeiro.")
    redirect_uri=str(request.base_url).rstrip("/")+"/api/marketplace/mercadolivre/callback"
    verifier=secrets.token_urlsafe(64)
    state=secrets.token_urlsafe(32)
    challenge=base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    with Session() as db:
        for key,value in (("ml_oauth_pkce_verifier",verifier),("ml_oauth_state",state)):
            row=db.get(Setting,key) or Setting(key=key); row.value=value; db.add(row)
        db.commit()
    from urllib.parse import urlencode
    return RedirectResponse("https://auth.mercadolivre.com.br/authorization?"+urlencode({"response_type":"code","client_id":client_id,"redirect_uri":redirect_uri,"state":state,"code_challenge":challenge,"code_challenge_method":"S256"}))

@app.get("/api/marketplace/mercadolivre/callback")
async def ml_callback(code: str, state: str, request: Request):
    redirect_uri=str(request.base_url).rstrip("/")+"/api/marketplace/mercadolivre/callback"
    with Session() as db:
        expected_state=setting(db,"ml_oauth_state")
        verifier=setting(db,"ml_oauth_pkce_verifier")
        if not expected_state or not secrets.compare_digest(state,expected_state) or not verifier:
            raise HTTPException(400,"A autorização expirou ou não corresponde à solicitação. Clique em Autorizar Mercado Livre e tente novamente.")
        data={"grant_type":"authorization_code","client_id":setting(db,"ml_client_id"),"client_secret":setting(db,"ml_client_secret"),"code":code,"redirect_uri":redirect_uri,"code_verifier":verifier}
        async with httpx.AsyncClient(timeout=20) as client: response=await client.post("https://api.mercadolibre.com/oauth/token",data=data)
        if response.is_error:
            details=response.json() if response.headers.get("content-type","").startswith("application/json") else {}
            reason=details.get("error_description") or details.get("message") or details.get("error")
            raise HTTPException(400,f"Mercado Livre recusou a autorização: {reason or 'verifique permissões e dados do aplicativo'}.")
        result=response.json()
        values={"ml_access_token":result["access_token"],"ml_refresh_token":result.get("refresh_token",""),"ml_token_expires":str(datetime.now(timezone.utc).timestamp()+result.get("expires_in",21600)),"ml_connected":"true"}
        for key,value in values.items(): row=db.get(Setting,key) or Setting(key=key); row.value=value; db.add(row)
        for key in ("ml_oauth_state","ml_oauth_pkce_verifier"):
            row=db.get(Setting,key)
            if row: db.delete(row)
        db.commit()
    return RedirectResponse("/")

@app.get("/api/schedules")
def get_schedules():
    with Session() as db: return [{"id": x.id, "time": x.time, "enabled": x.enabled} for x in db.scalars(select(Schedule).order_by(Schedule.time))]

@app.post("/api/schedules")
def add_schedule(payload: ScheduleIn):
    try: h, m = map(int, payload.time.split(":")); assert 0 <= h < 24 and 0 <= m < 60
    except Exception: raise HTTPException(400, "Use o formato HH:MM")
    with Session() as db:
        item = Schedule(time=payload.time, enabled=payload.enabled); db.add(item); db.commit(); db.refresh(item)
    refresh_jobs(); return {"id": item.id, "time": item.time, "enabled": item.enabled}

@app.post("/api/schedules/generate")
def generate_schedules(payload: dict):
    count=int(payload.get("count",6))
    if count not in {6,8}: raise HTTPException(400,"Escolha 6 ou 8 publicações por dia.")
    try:
        start=datetime.strptime(str(payload.get("start_time","09:00")),"%H:%M")
        end=datetime.strptime(str(payload.get("end_time","21:00")),"%H:%M")
    except ValueError: raise HTTPException(400,"Horários devem estar no formato HH:MM.")
    span=int((end-start).total_seconds()//60)
    if span < count-1: raise HTTPException(400,"A janela precisa comportar todos os horários.")
    times=[(start+timedelta(minutes=round(span*i/(count-1)))).strftime("%H:%M") for i in range(count)]
    with Session() as db:
        db.query(Schedule).delete()
        db.add_all([Schedule(time=t,enabled=True) for t in times]); db.commit()
    refresh_jobs()
    return {"count":count,"times":times}

@app.delete("/api/schedules/{schedule_id}")
def delete_schedule(schedule_id: int):
    with Session() as db:
        item = db.get(Schedule, schedule_id)
        if not item: raise HTTPException(404, "Horário não encontrado")
        db.delete(item); db.commit()
    refresh_jobs(); return {"ok": True}

@app.post("/api/offers/{offer_id}/telegram")
async def send_now(offer_id: int):
    with Session() as db:
        offer = db.get(Offer, offer_id)
        if not offer: raise HTTPException(404, "Oferta não encontrada")
        await telegram_send(db, offer); offer.status = "posted"; db.commit(); return {"ok": True}

@app.post("/api/ai/copy")
async def generate_copy(payload: OfferIn):
    with Session() as db:
        provider = setting(db, "ai_provider", "ollama")
        prompt = setting(db, "ai_prompt", DEFAULT_COPY_PROMPT) or DEFAULT_COPY_PROMPT
        price_details = f"Preço atual: R$ {payload.price:.2f}. Preço anterior informado: R$ {payload.original_price:.2f}. Redução calculada: {round((payload.original_price-payload.price)/payload.original_price*100)}%." if payload.original_price > payload.price > 0 else f"Preço informado: R$ {payload.price:.2f}."
        content = f"{prompt}\nProduto: {payload.title}\nCategoria: {payload.category}\n{price_details}"
        if provider == "gemini":
            key = setting(db, "ai_api_key")
            if not key: raise HTTPException(400, "Configure a chave Gemini.")
            url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent?key={key}"
            async with httpx.AsyncClient(timeout=30) as client: resp = await client.post(url, json={"contents": [{"parts": [{"text": content}]}]})
            if resp.is_error: raise HTTPException(502, "Falha ao consultar Gemini")
            return {"copy": clean_ai_copy(resp.json()["candidates"][0]["content"]["parts"][0]["text"],payload.title,payload.price,payload.original_price)}
        base = setting(db, "ollama_url", os.getenv("OLLAMA_URL", "http://localhost:11434"))
        async with httpx.AsyncClient(timeout=180) as client:
            try: resp = await client.post(base.rstrip("/") + "/api/generate", json={"model": "llama3.2:1b", "prompt": content, "stream": False, "options":{"num_predict":72,"temperature":0.75}})
            except httpx.HTTPError as exc: raise HTTPException(502, f"Não foi possível acessar Ollama: {exc}")
        if resp.is_error: raise HTTPException(502, "Falha ao consultar Ollama")
        return {"copy": clean_ai_copy(resp.json().get("response", ""),payload.title,payload.price,payload.original_price)}

@app.get("/api/metrics")
def metrics():
    with Session() as db:
        offers = db.scalars(select(Offer)).all(); clicks = db.scalars(select(Click).order_by(Click.clicked_at)).all()
        by_offer = {o.id: sum(c.offer_id == o.id for c in clicks) for o in offers}
        by_cat = {}
        for o in offers: by_cat[o.category] = by_cat.get(o.category, 0) + by_offer[o.id] * o.commission
        hours = {}
        for c in clicks: hours[c.clicked_at.strftime("%H:00")] = hours.get(c.clicked_at.strftime("%H:00"), 0) + 1
        days = {}
        for c in clicks: days[c.clicked_at.strftime("%Y-%m-%d")] = days.get(c.clicked_at.strftime("%Y-%m-%d"), 0) + 1
        top = sorted(offers, key=lambda o: by_offer[o.id], reverse=True)[:5]
        return {"total_clicks": len(clicks), "offers_created": len(offers), "best_category": max(by_cat, key=by_cat.get) if by_cat else "—",
                "peak_hour": max(hours, key=hours.get) if hours else "—", "daily_clicks": [{"day": k, "clicks": v} for k, v in days.items()],
                "top_offers": [{"title": o.title, "clicks": by_offer[o.id]} for o in top],
                "insight": f"Priorize {max(by_cat, key=by_cat.get)}" if by_cat else "Cadastre ofertas e acompanhe os cliques para gerar insights."}
