from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException, Response
from sqlalchemy.orm import Session

from app import schemas, security
from app.database import get_db
from app.models import User
from app.otp import send_otp_email

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/signup", response_model=schemas.MessageResponse)
def signup(payload: schemas.SignupRequest, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == payload.email).first():
        raise HTTPException(400, "Email already registered")

    otp = security.generate_otp()
    user = User(
        email=payload.email,
        hashed_password=security.hash_password(payload.password),
        is_verified=False,
        otp_hash=security.hash_password(otp),
        otp_purpose="signup",
        otp_expires_at=security.otp_expiry(),
    )
    db.add(user)
    db.commit()

    send_otp_email(payload.email, otp, "signup")
    return {"message": "Account created. Check your email for the OTP to verify."}


@router.post("/verify-otp", response_model=schemas.MessageResponse)
def verify_otp(payload: schemas.OTPVerifyRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    _validate_otp(user, payload.otp, expected_purpose="signup")

    user.is_verified = True
    user.otp_hash = None
    user.otp_purpose = None
    user.otp_expires_at = None
    db.commit()
    return {"message": "Email verified. You can now log in."}


@router.post("/login", response_model=schemas.MessageResponse)
def login(payload: schemas.LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user or not security.verify_password(payload.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_verified:
        raise HTTPException(403, "Email not verified. Please verify OTP first.")

    token = security.create_access_token({"sub": user.email})
    response.set_cookie("access_token", token, httponly=True, max_age=3600)
    return {"message": "Login successful"}


@router.post("/logout", response_model=schemas.MessageResponse)
def logout(response: Response):
    response.delete_cookie("access_token")
    return {"message": "Logged out"}


@router.post("/forgot-password", response_model=schemas.MessageResponse)
def forgot_password(payload: schemas.ForgotPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    if not user:
        # Don't reveal whether the email exists
        return {"message": "If that email is registered, an OTP has been sent."}

    otp = security.generate_otp()
    user.otp_hash = security.hash_password(otp)
    user.otp_purpose = "reset"
    user.otp_expires_at = security.otp_expiry()
    db.commit()

    send_otp_email(payload.email, otp, "reset")
    return {"message": "If that email is registered, an OTP has been sent."}


@router.post("/reset-password", response_model=schemas.MessageResponse)
def reset_password(payload: schemas.ResetPasswordRequest, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == payload.email).first()
    _validate_otp(user, payload.otp, expected_purpose="reset")

    user.hashed_password = security.hash_password(payload.new_password)
    user.otp_hash = None
    user.otp_purpose = None
    user.otp_expires_at = None
    db.commit()
    return {"message": "Password reset. You can now log in."}


def _validate_otp(user: User | None, otp: str, expected_purpose: str) -> None:
    if not user or not user.otp_hash or user.otp_purpose != expected_purpose:
        raise HTTPException(400, "Invalid or expired OTP")
    if user.otp_expires_at < datetime.utcnow():
        raise HTTPException(400, "OTP has expired")
    if not security.verify_password(otp, user.otp_hash):
        raise HTTPException(400, "Incorrect OTP")
