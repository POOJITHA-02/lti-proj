from dotenv import load_dotenv

load_dotenv()

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from app import security
from app.database import Base, engine
from app.routers import auth

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Signature-Driven Inventory")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
templates = Jinja2Templates(directory="app/templates")

app.include_router(auth.router)


def current_user(request: Request) -> str | None:
    token = request.cookies.get("access_token")
    if not token:
        return None
    return security.decode_access_token(token)


@app.get("/")
def root(request: Request):
    if current_user(request):
        return RedirectResponse("/dashboard")
    return RedirectResponse("/login")


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request})


@app.get("/signup")
def signup_page(request: Request):
    return templates.TemplateResponse("signup.html", {"request": request})


@app.get("/verify-otp")
def verify_otp_page(request: Request):
    return templates.TemplateResponse("verify_otp.html", {"request": request})


@app.get("/forgot-password")
def forgot_password_page(request: Request):
    return templates.TemplateResponse("forgot_password.html", {"request": request})


@app.get("/reset-password")
def reset_password_page(request: Request):
    return templates.TemplateResponse("reset_password.html", {"request": request})


@app.get("/dashboard")
def dashboard_page(request: Request):
    email = current_user(request)
    if not email:
        return RedirectResponse("/login")
    return templates.TemplateResponse("dashboard.html", {"request": request, "email": email})
