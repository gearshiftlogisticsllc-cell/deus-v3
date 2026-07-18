from app.api.auth_routes import router as auth_router
from app.api.lead_routes import router as lead_router
from app.api.support_routes import router as support_router

__all__ = ["auth_router", "lead_router", "support_router"]
