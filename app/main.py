from fastapi import FastAPI

from app.api.routes import health, products

app = FastAPI(title="ALICARTUNING Marketplace AI Manager", version="0.1.0")

app.include_router(health.router)
app.include_router(products.router)
