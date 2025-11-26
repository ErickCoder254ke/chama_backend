from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, Request, BackgroundTasks
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from fastapi.responses import JSONResponse
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, EmailStr
from typing import List, Optional
import uuid
from datetime import datetime, timedelta
import jwt
from passlib.context import CryptContext
from bson import ObjectId
import random
import string
from io import BytesIO
from reportlab.lib.pagesizes import letter, A4
from reportlab.platypus import SimpleDocTemplate, Table, TableStyle, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib import colors
import base64
import asyncio
import calendar
from loan_calculator import (
    generate_amortization_schedule,
    calculate_monthly_payment,
    validate_loan_parameters as validate_loan_params,
    calculate_payment_breakdown,
    calculate_affordability
)

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Security
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
SECRET_KEY = os.getenv("SECRET_KEY", "chamake_secret_key_change_in_production")
ALGORITHM = "HS256"
security = HTTPBearer()

app = FastAPI(title="ChamaKe API", version="1.0.0")

# Add GZIP compression for Render free tier
from starlette.middleware.gzip import GZIPMiddleware
app.add_middleware(GZIPMiddleware, minimum_size=500)

api_router = APIRouter(prefix="/api")

# Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Global exception handler
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.error(f"Global exception: {str(exc)}", exc_info=True)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Please contact support."}
    )

@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail}
    )

# Helper functions
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict, expires_delta: timedelta = timedelta(days=30)):
    to_encode = data.copy()
    expire = datetime.utcnow() + expires_delta
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def generate_invite_code() -> str:
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))

def generate_otp() -> str:
    return ''.join(random.choices(string.digits, k=6))

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id = payload.get("user_id")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        user = await db.users.find_one({"_id": ObjectId(user_id)})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expired")
    except jwt.JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# Pydantic Models
class UserRegister(BaseModel):
    name: str
    phone: str
    email: Optional[EmailStr] = None
    password: str

class UserLogin(BaseModel):
    phone: str
    password: str

class OTPVerify(BaseModel):
    phone: str
    otp: str

class ChamaCreate(BaseModel):
    name: str
    logo: Optional[str] = None  # base64
    meeting_frequency: str  # weekly, monthly
    contribution_amount: float
    contribution_deadline: int  # day of month/week

class ChamaJoin(BaseModel):
    invite_code: str

class ContributionCreate(BaseModel):
    member_id: str
    amount: float
    due_date: str

class ContributionUpdate(BaseModel):
    status: str  # paid/pending
    paid_date: Optional[str] = None

class ContributionPayment(BaseModel):
    payment_method: str  # mpesa, bank_transfer, cash
    transaction_reference: Optional[str] = None
    receipt_image: Optional[str] = None  # base64
    notes: Optional[str] = None

class PaymentVerification(BaseModel):
    contribution_id: str
    verified: bool
    admin_notes: Optional[str] = None

class LoanRequest(BaseModel):
    chama_id: str
    amount: float
    reason: str
    repayment_period: int  # months (changed from days)
    interest_rate: float = 12.0  # annual rate
    payment_frequency: str = "monthly"  # monthly, weekly, biweekly

class LoanApproval(BaseModel):
    loan_id: str
    status: str  # approved/rejected

class RepaymentCreate(BaseModel):
    loan_id: str
    amount: float

class AnnouncementCreate(BaseModel):
    chama_id: str
    title: str
    message: str

class MemberRoleUpdate(BaseModel):
    member_id: str
    role: str  # admin, treasurer, secretary, member

class ProfileUpdate(BaseModel):
    name: Optional[str] = None
    email: Optional[EmailStr] = None
    profile_picture: Optional[str] = None  # base64

class PasswordChange(BaseModel):
    current_password: str
    new_password: str

class HistoricalContribution(BaseModel):
    due_date: str
    amount: float
    paid_date: Optional[str] = None
    status: str  # paid/pending
    notes: Optional[str] = None

class BulkHistoricalContributions(BaseModel):
    member_id: str
    contributions: List[HistoricalContribution]

class AutoContributionSettings(BaseModel):
    enabled: bool
    contribution_day: int  # day of month (1-31)
    contribution_amount: float
    auto_create_contributions: bool
    fine_amount: float = 0.0  # fine for late/unpaid contributions

class MerryGoRoundCreate(BaseModel):
    chama_id: str
    name: str
    contribution_amount: float
    frequency: str  # weekly, monthly
    member_order: List[str]  # list of member_ids in rotation order
    start_date: str
    funding_source: str  # contribution, balance
    description: Optional[str] = None

class MerryGoRoundUpdate(BaseModel):
    status: str  # active, completed, paused

class MerryGoRoundAdvance(BaseModel):
    round_id: str
    payout_amount: float
    payment_method: str  # mpesa, bank_transfer, cash
    transaction_reference: Optional[str] = None
    notes: Optional[str] = None

class InvestmentGoalCreate(BaseModel):
    chama_id: str
    name: str
    description: Optional[str] = None
    target_amount: float
    deadline: str  # YYYY-MM-DD
    goal_type: str  # group, personal
    category: str  # real_estate, business, stocks, bonds, emergency_fund, other
    icon: Optional[str] = None
    color: Optional[str] = None

class InvestmentGoalUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    target_amount: Optional[float] = None
    deadline: Optional[str] = None
    status: Optional[str] = None  # active, completed, cancelled
    category: Optional[str] = None
    icon: Optional[str] = None
    color: Optional[str] = None

class GoalContributionCreate(BaseModel):
    goal_id: str
    amount: float
    contribution_date: Optional[str] = None
    notes: Optional[str] = None
    funding_source: Optional[str] = "external"  # "group_balance" or "external"

class InvestmentCreate(BaseModel):
    chama_id: str
    name: str
    description: Optional[str] = None
    investment_type: str  # real_estate, stocks, bonds, business, mutual_fund, treasury_bill, other
    initial_amount: float
    current_value: float
    purchase_date: str
    maturity_date: Optional[str] = None
    risk_level: str  # low, medium, high
    notes: Optional[str] = None
    linked_goal_id: Optional[str] = None
    funding_source: Optional[str] = "external"  # "group_balance" or "external"

class InvestmentUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    current_value: Optional[float] = None
    maturity_date: Optional[str] = None
    status: Optional[str] = None  # active, matured, sold, liquidated
    notes: Optional[str] = None

class InvestmentReturnCreate(BaseModel):
    investment_id: str
    return_amount: float
    return_date: str
    return_type: str  # dividend, interest, capital_gain, sale_proceeds
    notes: Optional[str] = None

class MeetingCreate(BaseModel):
    chama_id: str
    title: str
    description: Optional[str] = None
    agenda: Optional[str] = None
    start_time: str  # ISO datetime
    end_time: Optional[str] = None
    location: Optional[str] = None
    virtual_link: Optional[str] = None
    recurring: bool = False
    recurring_frequency: Optional[str] = None  # weekly, monthly

class MeetingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    agenda: Optional[str] = None
    start_time: Optional[str] = None
    end_time: Optional[str] = None
    location: Optional[str] = None
    virtual_link: Optional[str] = None
    status: Optional[str] = None  # scheduled, cancelled, completed

class RSVPCreate(BaseModel):
    status: str  # attending, not_attending, maybe

class AttendanceCreate(BaseModel):
    meeting_id: str
    method: str = "manual"  # manual, qr
    notes: Optional[str] = None

class MinutesCreate(BaseModel):
    meeting_id: str
    content: str
    decisions: Optional[str] = None
    action_items: Optional[str] = None

class WelfareSettingsUpdate(BaseModel):
    enabled: bool = True
    monthly_contribution: Optional[float] = None
    max_request_amount: Optional[float] = 50000
    approval_threshold: Optional[float] = 0.75  # 75% approval required
    processing_days: Optional[int] = 5

class WelfareContributionCreate(BaseModel):
    amount: float
    contribution_date: Optional[str] = None
    notes: Optional[str] = None

class WelfareRequestCreate(BaseModel):
    chama_id: str
    request_type: str  # medical, bereavement, education, emergency
    amount: float
    reason: str
    supporting_documents: Optional[List[str]] = None  # base64 images/docs
    description: Optional[str] = None

class WelfareRequestUpdate(BaseModel):
    status: Optional[str] = None  # pending, approved, rejected, disbursed
    admin_notes: Optional[str] = None
    disbursement_date: Optional[str] = None
    disbursement_method: Optional[str] = None
    transaction_reference: Optional[str] = None

class WelfareVoteCreate(BaseModel):
    request_id: str
    vote: str  # approve, reject
    comment: Optional[str] = None

class WelfareDisbursementCreate(BaseModel):
    request_id: str
    disbursement_method: str  # mpesa, bank_transfer, cash
    disbursement_source: str  # sacco_account, cash
    transaction_reference: Optional[str] = None
    notes: Optional[str] = None

# Share Management Models
class ShareSettingsUpdate(BaseModel):
    enabled: bool = True
    total_shares: Optional[int] = 1000
    share_price: Optional[float] = 5000.0
    min_shares_per_member: Optional[int] = 1
    max_shares_per_member: Optional[int] = None
    allow_share_transfer: Optional[bool] = True

class SharePurchaseCreate(BaseModel):
    chama_id: str
    member_id: str
    quantity: int
    price_per_share: float
    total_amount: float
    payment_method: str  # mpesa, bank_transfer, cash, group_balance
    transaction_reference: Optional[str] = None
    notes: Optional[str] = None

class ShareTransferCreate(BaseModel):
    chama_id: str
    from_member_id: str
    to_member_id: str
    quantity: int
    price_per_share: Optional[float] = None
    total_amount: Optional[float] = None
    reason: Optional[str] = None
    notes: Optional[str] = None

class DividendDeclaration(BaseModel):
    chama_id: str
    total_dividend_amount: float
    dividend_per_share: float
    declaration_date: str
    payment_date: str
    financial_year: str
    notes: Optional[str] = None

# Authentication Endpoints
@api_router.post("/auth/register")
async def register(user: UserRegister):
    # Check if user exists
    existing_user = await db.users.find_one({"phone": user.phone})
    if existing_user:
        raise HTTPException(status_code=400, detail="Phone number already registered")
    
    # Generate OTP (mock - in production, send via SMS)
    otp = generate_otp()
    
    # Store user with pending status
    user_dict = {
        "name": user.name,
        "phone": user.phone,
        "email": user.email,
        "password_hash": hash_password(user.password),
        "otp": otp,
        "verified": False,
        "profile_picture": None,
        "created_at": datetime.utcnow().isoformat()
    }
    
    result = await db.users.insert_one(user_dict)
    logger.info(f"Mock OTP for {user.phone}: {otp}")  # In production, send via SMS
    
    return {"message": "Registration successful. OTP sent.", "otp": otp, "user_id": str(result.inserted_id)}

@api_router.post("/auth/verify-otp")
async def verify_otp(data: OTPVerify):
    user = await db.users.find_one({"phone": data.phone})
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    
    # Mock OTP verification - accept any 6-digit code for MVP
    if len(data.otp) == 6:
        await db.users.update_one(
            {"_id": user["_id"]},
            {"$set": {"verified": True, "otp": None}}
        )
        
        token = create_access_token({"user_id": str(user["_id"])})
        return {
            "token": token,
            "user": {
                "id": str(user["_id"]),
                "name": user["name"],
                "phone": user["phone"],
                "email": user.get("email"),
                "profile_picture": user.get("profile_picture")
            }
        }
    else:
        raise HTTPException(status_code=400, detail="Invalid OTP")

@api_router.post("/auth/login")
async def login(credentials: UserLogin):
    user = await db.users.find_one({"phone": credentials.phone})
    if not user or not verify_password(credentials.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    if not user.get("verified", False):
        raise HTTPException(status_code=401, detail="Phone number not verified")
    
    token = create_access_token({"user_id": str(user["_id"])})
    return {
        "token": token,
        "user": {
            "id": str(user["_id"]),
            "name": user["name"],
            "phone": user["phone"],
            "email": user.get("email"),
            "profile_picture": user.get("profile_picture")
        }
    }

@api_router.get("/auth/me")
async def get_me(current_user: dict = Depends(get_current_user)):
    return {
        "id": str(current_user["_id"]),
        "name": current_user["name"],
        "phone": current_user["phone"],
        "email": current_user.get("email", ""),
        "profile_picture": current_user.get("profile_picture", "")
    }

@api_router.put("/auth/update-profile")
async def update_profile(data: ProfileUpdate, current_user: dict = Depends(get_current_user)):
    update_data = {}

    if data.name:
        update_data["name"] = data.name
    if data.email:
        update_data["email"] = data.email
    if data.profile_picture:
        update_data["profile_picture"] = data.profile_picture

    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")

    await db.users.update_one(
        {"_id": current_user["_id"]},
        {"$set": update_data}
    )

    # Get updated user
    updated_user = await db.users.find_one({"_id": current_user["_id"]})

    return {
        "message": "Profile updated successfully",
        "user": {
            "id": str(updated_user["_id"]),
            "name": updated_user["name"],
            "phone": updated_user["phone"],
            "email": updated_user.get("email", ""),
            "profile_picture": updated_user.get("profile_picture", "")
        }
    }

@api_router.put("/auth/change-password")
async def change_password(data: PasswordChange, current_user: dict = Depends(get_current_user)):
    # Verify current password
    if not verify_password(data.current_password, current_user["password_hash"]):
        raise HTTPException(status_code=400, detail="Current password is incorrect")

    # Validate new password
    if len(data.new_password) < 6:
        raise HTTPException(status_code=400, detail="New password must be at least 6 characters")

    # Update password
    new_password_hash = hash_password(data.new_password)
    await db.users.update_one(
        {"_id": current_user["_id"]},
        {"$set": {"password_hash": new_password_hash}}
    )

    return {"message": "Password changed successfully"}

# Chama Endpoints
@api_router.post("/chamas/create")
async def create_chama(chama: ChamaCreate, current_user: dict = Depends(get_current_user)):
    invite_code = generate_invite_code()
    
    chama_dict = {
        "name": chama.name,
        "logo": chama.logo,
        "admin_id": str(current_user["_id"]),
        "invite_code": invite_code,
        "meeting_frequency": chama.meeting_frequency,
        "contribution_amount": chama.contribution_amount,
        "contribution_deadline": chama.contribution_deadline,
        "created_at": datetime.utcnow().isoformat()
    }
    
    result = await db.chamas.insert_one(chama_dict)
    chama_id = str(result.inserted_id)
    
    # Add creator as admin member
    member_dict = {
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "role": "admin",
        "status": "active",
        "joined_at": datetime.utcnow().isoformat()
    }
    await db.members.insert_one(member_dict)
    
    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "action": "create_chama",
        "details": f"Created Chama: {chama.name}",
        "timestamp": datetime.utcnow().isoformat()
    })
    
    return {"chama_id": chama_id, "invite_code": invite_code}

@api_router.post("/chamas/join")
async def join_chama(data: ChamaJoin, current_user: dict = Depends(get_current_user)):
    chama = await db.chamas.find_one({"invite_code": data.invite_code})
    if not chama:
        raise HTTPException(status_code=404, detail="Invalid invite code")
    
    chama_id = str(chama["_id"])
    
    # Check if already a member
    existing_member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"])
    })
    if existing_member:
        raise HTTPException(status_code=400, detail="Already a member of this Chama")
    
    # Add as member
    member_dict = {
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "role": "member",
        "status": "active",
        "joined_at": datetime.utcnow().isoformat()
    }
    await db.members.insert_one(member_dict)
    
    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "action": "join_chama",
        "details": f"Joined Chama: {chama['name']}",
        "timestamp": datetime.utcnow().isoformat()
    })
    
    return {"message": "Successfully joined Chama", "chama_id": chama_id}

@api_router.get("/chamas/my-chamas")
async def get_my_chamas(current_user: dict = Depends(get_current_user)):
    # Find all chamas where user is a member (limit to prevent huge responses)
    members = await db.members.find({
        "user_id": str(current_user["_id"]),
        "status": "active"
    }).limit(50).to_list(50)
    
    chamas = []
    for member in members:
        chama = await db.chamas.find_one({"_id": ObjectId(member["chama_id"])})
        if chama:
            # Count members
            member_count = await db.members.count_documents({
                "chama_id": member["chama_id"],
                "status": "active"
            })
            
            chamas.append({
                "id": str(chama["_id"]),
                "name": chama["name"],
                "logo": chama.get("logo"),
                "role": member["role"],
                "member_count": member_count,
                "contribution_amount": chama.get("contribution_amount", 0)
            })
    
    return chamas

@api_router.get("/chamas/{chama_id}")
async def get_chama_details(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")
    
    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")
    
    return {
        "id": str(chama["_id"]),
        "name": chama["name"],
        "logo": chama.get("logo"),
        "admin_id": chama["admin_id"],
        "invite_code": chama["invite_code"],
        "meeting_frequency": chama["meeting_frequency"],
        "contribution_amount": chama["contribution_amount"],
        "contribution_deadline": chama["contribution_deadline"],
        "user_role": member["role"]
    }

# Member Endpoints
@api_router.get("/members/{chama_id}")
async def get_chama_members(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")
    
    members = await db.members.find({"chama_id": chama_id, "status": "active"}).limit(200).to_list(200)

    result = []
    for m in members:
        # Use field projection to only get needed fields
        user = await db.users.find_one(
            {"_id": ObjectId(m["user_id"])},
            {"name": 1, "phone": 1}  # Exclude profile_picture to reduce size
        )
        if user:
            result.append({
                "member_id": str(m["_id"]),
                "user_id": str(user["_id"]),
                "name": user["name"],
                "phone": user["phone"],
                "role": m["role"],
                "joined_at": m["joined_at"]
            })
    
    return result

@api_router.put("/members/update-role")
async def update_member_role(data: MemberRoleUpdate, current_user: dict = Depends(get_current_user)):
    # Get member to update
    target_member = await db.members.find_one({"_id": ObjectId(data.member_id)})
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found")
    
    # Verify current user is admin
    current_member = await db.members.find_one({
        "chama_id": target_member["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not current_member or current_member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update roles")
    
    await db.members.update_one(
        {"_id": ObjectId(data.member_id)},
        {"$set": {"role": data.role}}
    )
    
    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": target_member["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "update_member_role",
        "details": f"Updated member role to {data.role}",
        "timestamp": datetime.utcnow().isoformat()
    })
    
    return {"message": "Role updated successfully"}

@api_router.delete("/members/{member_id}")
async def remove_member(member_id: str, current_user: dict = Depends(get_current_user)):
    # Get member to remove
    target_member = await db.members.find_one({"_id": ObjectId(member_id)})
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found")
    
    # Verify current user is admin
    current_member = await db.members.find_one({
        "chama_id": target_member["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not current_member or current_member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can remove members")
    
    await db.members.update_one(
        {"_id": ObjectId(member_id)},
        {"$set": {"status": "removed"}}
    )
    
    return {"message": "Member removed successfully"}

# Contribution Endpoints
@api_router.post("/contributions/create")
async def create_contribution(chama_id: str, data: ContributionCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create contributions")
    
    # Verify member exists and belongs to chama
    member = await db.members.find_one({
        "_id": ObjectId(data.member_id),
        "chama_id": chama_id,
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=404, detail="Member not found in this Chama")

    contribution_dict = {
        "chama_id": chama_id,
        "member_id": data.member_id,
        "amount": data.amount,
        "due_date": data.due_date,
        "status": "pending",
        "paid_date": None,
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.contributions.insert_one(contribution_dict)
    return {"contribution_id": str(result.inserted_id), "message": "Contribution created successfully"}

@api_router.get("/contributions/{chama_id}")
async def get_contributions(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")
    
    # Limit contributions to prevent huge responses
    contributions = await db.contributions.find(
        {"chama_id": chama_id}
    ).sort("due_date", -1).limit(500).to_list(500)

    result = []
    for c in contributions:
        # Get member details - member_id is actually the member document _id
        member_doc = await db.members.find_one(
            {"_id": ObjectId(c["member_id"])},
            {"user_id": 1}
        )
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one(
                {"_id": ObjectId(member_doc["user_id"])},
                {"name": 1}
            )
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(c["_id"]),
            "member_id": c["member_id"],
            "member_name": member_name,
            "amount": c["amount"],
            "due_date": c["due_date"],
            "paid_date": c.get("paid_date"),
            "status": c["status"]
        })

    return result

@api_router.put("/contributions/{contribution_id}")
async def update_contribution(contribution_id: str, data: ContributionUpdate, current_user: dict = Depends(get_current_user)):
    contribution = await db.contributions.find_one({"_id": ObjectId(contribution_id)})
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": contribution["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update contributions")

    update_data = {"status": data.status}
    if data.paid_date:
        update_data["paid_date"] = data.paid_date

    await db.contributions.update_one(
        {"_id": ObjectId(contribution_id)},
        {"$set": update_data}
    )

    return {"message": "Contribution updated successfully"}

@api_router.delete("/contributions/{contribution_id}")
async def delete_contribution(contribution_id: str, current_user: dict = Depends(get_current_user)):
    contribution = await db.contributions.find_one({"_id": ObjectId(contribution_id)})
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": contribution["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete contributions")

    # Check if this contribution has been consolidated into arrears
    if contribution.get("consolidated_into_arrears"):
        raise HTTPException(
            status_code=400,
            detail="Cannot delete contribution that has been consolidated into arrears. Delete the arrears contribution instead."
        )

    # Check if this is an arrears contribution with breakdown
    if contribution.get("is_arrears") and contribution.get("arrears_breakdown"):
        # If this is an arrears contribution, we need to un-consolidate the source contributions
        arrears_breakdown = contribution.get("arrears_breakdown", {})
        unpaid_periods = arrears_breakdown.get("unpaid_periods", [])

        if unpaid_periods:
            # Get the IDs of consolidated contributions
            consolidated_ids = [ObjectId(period["contribution_id"]) for period in unpaid_periods]

            # Un-mark them as consolidated
            if consolidated_ids:
                await db.contributions.update_many(
                    {"_id": {"$in": consolidated_ids}},
                    {"$unset": {
                        "consolidated_into_arrears": "",
                        "consolidated_date": "",
                        "arrears_contribution_due_date": ""
                    }}
                )
                logger.info(f"Un-consolidated {len(consolidated_ids)} contributions when deleting arrears contribution {contribution_id}")

    # Get member details for logging
    member_doc = await db.members.find_one({"_id": ObjectId(contribution["member_id"])})
    member_name = "Unknown"
    if member_doc:
        user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
        member_name = user["name"] if user else "Unknown"

    # Delete associated payment records if any
    if contribution.get("payment_id"):
        payment_result = await db.payments.delete_one({"_id": ObjectId(contribution["payment_id"])})
        if payment_result.deleted_count > 0:
            logger.info(f"Deleted payment record {contribution['payment_id']} associated with contribution {contribution_id}")

    # Store contribution details for audit log before deletion
    contribution_amount = contribution["amount"]
    contribution_status = contribution["status"]
    contribution_due_date = contribution.get("due_date", "N/A")

    # Delete the contribution
    await db.contributions.delete_one({"_id": ObjectId(contribution_id)})

    # Create audit log
    await db.audit_logs.insert_one({
        "chama_id": contribution["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "delete_contribution",
        "details": f"Deleted {contribution_status} contribution for {member_name}: KES {contribution_amount} (due: {contribution_due_date})",
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": {
            "contribution_id": contribution_id,
            "member_id": contribution["member_id"],
            "member_name": member_name,
            "amount": contribution_amount,
            "status": contribution_status,
            "due_date": contribution_due_date,
            "was_arrears": contribution.get("is_arrears", False)
        }
    })

    logger.info(f"Admin {current_user['name']} deleted {contribution_status} contribution {contribution_id} for {member_name} (KES {contribution_amount})")

    return {
        "message": "Contribution deleted successfully",
        "details": {
            "member_name": member_name,
            "amount": contribution_amount,
            "status": contribution_status,
            "due_date": contribution_due_date
        }
    }

@api_router.post("/contributions/{contribution_id}/pay")
async def make_payment(contribution_id: str, data: ContributionPayment, current_user: dict = Depends(get_current_user)):
    contribution = await db.contributions.find_one({"_id": ObjectId(contribution_id)})
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")

    # Verify this is the member's own contribution
    member = await db.members.find_one({
        "_id": ObjectId(contribution["member_id"]),
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="You can only pay your own contributions")

    # Create payment record
    payment_dict = {
        "contribution_id": contribution_id,
        "chama_id": contribution["chama_id"],
        "member_id": contribution["member_id"],
        "user_id": str(current_user["_id"]),
        "amount": contribution["amount"],
        "payment_method": data.payment_method,
        "transaction_reference": data.transaction_reference,
        "receipt_image": data.receipt_image,
        "notes": data.notes,
        "status": "pending_verification",
        "verified": False,
        "verified_by": None,
        "admin_notes": None,
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.payments.insert_one(payment_dict)

    # Update contribution status to pending_verification
    await db.contributions.update_one(
        {"_id": ObjectId(contribution_id)},
        {"$set": {
            "status": "pending_verification",
            "payment_id": str(result.inserted_id),
            "payment_date": datetime.utcnow().isoformat()
        }}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": contribution["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "make_payment",
        "details": f"Payment submitted for {data.payment_method}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "payment_id": str(result.inserted_id),
        "message": "Payment submitted successfully. Awaiting admin verification."
    }

@api_router.get("/contributions/my-contributions/{chama_id}")
async def get_my_contributions(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership and get member record
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get contributions for this member (limit to reduce response size)
    contributions = await db.contributions.find({
        "chama_id": chama_id,
        "member_id": str(member["_id"])
    }).sort("due_date", -1).limit(200).to_list(200)

    result = []
    for c in contributions:
        contribution_data = {
            "id": str(c["_id"]),
            "amount": c["amount"],
            "due_date": c["due_date"],
            "status": c["status"],
            "paid_date": c.get("paid_date"),
            "payment_method": None,
            "transaction_reference": None,
            "has_receipt": False  # Changed from receipt_image to boolean
        }

        # Get payment details if exists (exclude receipt_image for list view)
        if c.get("payment_id"):
            payment = await db.payments.find_one(
                {"_id": ObjectId(c["payment_id"])},
                {"payment_method": 1, "transaction_reference": 1, "verified": 1, "admin_notes": 1, "receipt_image": 1}
            )
            if payment:
                contribution_data.update({
                    "payment_method": payment.get("payment_method"),
                    "transaction_reference": payment.get("transaction_reference"),
                    "has_receipt": bool(payment.get("receipt_image")),  # Only indicate presence
                    "verified": payment.get("verified", False),
                    "admin_notes": payment.get("admin_notes")
                })

        result.append(contribution_data)

    return result

@api_router.post("/contributions/verify-payment")
async def verify_payment(data: PaymentVerification, current_user: dict = Depends(get_current_user)):
    contribution = await db.contributions.find_one({"_id": ObjectId(data.contribution_id)})
    if not contribution:
        raise HTTPException(status_code=404, detail="Contribution not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": contribution["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can verify payments")

    # Update payment record
    if contribution.get("payment_id"):
        payment_status = "verified" if data.verified else "rejected"
        await db.payments.update_one(
            {"_id": ObjectId(contribution["payment_id"])},
            {"$set": {
                "verified": data.verified,
                "verified_by": str(current_user["_id"]),
                "admin_notes": data.admin_notes,
                "verified_at": datetime.utcnow().isoformat(),
                "status": payment_status
            }}
        )

    # Update contribution status
    new_status = "paid" if data.verified else "pending"
    update_data = {"status": new_status}
    if data.verified:
        update_data["paid_date"] = datetime.utcnow().isoformat()

    await db.contributions.update_one(
        {"_id": ObjectId(data.contribution_id)},
        {"$set": update_data}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": contribution["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "verify_payment",
        "details": f"Payment {'verified' if data.verified else 'rejected'}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"message": f"Payment {'verified' if data.verified else 'rejected'} successfully"}

@api_router.get("/payments/pending/{chama_id}")
async def get_pending_payments(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view pending payments")

    # Get pending payments (limit and exclude base64)
    payments = await db.payments.find({
        "chama_id": chama_id,
        "status": "pending_verification"
    }).sort("created_at", -1).limit(100).to_list(100)

    result = []
    for p in payments:
        # Get member details
        member_doc = await db.members.find_one(
            {"_id": ObjectId(p["member_id"])},
            {"user_id": 1}
        )
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one(
                {"_id": ObjectId(member_doc["user_id"])},
                {"name": 1}
            )
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(p["_id"]),
            "contribution_id": p["contribution_id"],
            "member_name": member_name,
            "amount": p["amount"],
            "payment_method": p["payment_method"],
            "transaction_reference": p.get("transaction_reference"),
            "has_receipt": bool(p.get("receipt_image")),  # Don't send base64 in list
            "notes": p.get("notes"),
            "created_at": p["created_at"]
        })

    return result

# Historical Contributions & Auto-Contribution Endpoints
@api_router.post("/contributions/bulk-historical/{chama_id}")
async def add_bulk_historical_contributions(
    chama_id: str,
    data: BulkHistoricalContributions,
    current_user: dict = Depends(get_current_user)
):
    # Verify admin
    admin_member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not admin_member or admin_member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can add historical contributions")

    # Verify target member exists
    target_member = await db.members.find_one({
        "_id": ObjectId(data.member_id),
        "chama_id": chama_id,
        "status": "active"
    })
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found in this Chama")

    # Get user details for logging
    user = await db.users.find_one({"_id": ObjectId(target_member["user_id"])})
    member_name = user["name"] if user else "Unknown"

    # Validate contributions
    if not data.contributions or len(data.contributions) == 0:
        raise HTTPException(status_code=400, detail="At least one contribution is required")

    # Insert contributions in bulk
    contributions_to_insert = []
    for contrib in data.contributions:
        contribution_dict = {
            "chama_id": chama_id,
            "member_id": data.member_id,
            "amount": contrib.amount,
            "due_date": contrib.due_date,
            "status": contrib.status,
            "paid_date": contrib.paid_date,
            "notes": contrib.notes,
            "is_historical": True,
            "added_by": str(current_user["_id"]),
            "created_at": datetime.utcnow().isoformat()
        }
        contributions_to_insert.append(contribution_dict)

    if contributions_to_insert:
        result = await db.contributions.insert_many(contributions_to_insert)
        inserted_count = len(result.inserted_ids)

        # Audit log
        await db.audit_logs.insert_one({
            "chama_id": chama_id,
            "user_id": str(current_user["_id"]),
            "action": "add_historical_contributions",
            "details": f"Added {inserted_count} historical contributions for {member_name}",
            "timestamp": datetime.utcnow().isoformat()
        })

        return {
            "message": f"Successfully added {inserted_count} historical contributions",
            "count": inserted_count,
            "contribution_ids": [str(id) for id in result.inserted_ids]
        }

    return {"message": "No contributions to add", "count": 0}

@api_router.get("/contributions/historical/{chama_id}/{member_id}")
async def get_historical_contributions(
    chama_id: str,
    member_id: str,
    current_user: dict = Depends(get_current_user)
):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get historical contributions
    historical_contribs = await db.contributions.find({
        "chama_id": chama_id,
        "member_id": member_id,
        "is_historical": True
    }).sort("due_date", 1).to_list(1000)

    result = []
    for c in historical_contribs:
        result.append({
            "id": str(c["_id"]),
            "amount": c["amount"],
            "due_date": c["due_date"],
            "paid_date": c.get("paid_date"),
            "status": c["status"],
            "notes": c.get("notes"),
            "created_at": c["created_at"]
        })

    return result

@api_router.get("/chamas/{chama_id}/auto-contribution-settings")
async def get_auto_contribution_settings(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view auto-contribution settings")

    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    # Return current settings or defaults
    return {
        "enabled": chama.get("auto_contribution_enabled", False),
        "contribution_day": chama.get("contribution_deadline", 1),
        "contribution_amount": chama.get("contribution_amount", 0),
        "auto_create_contributions": chama.get("auto_create_contributions", True),
        "fine_amount": chama.get("fine_amount", 0.0)
    }

@api_router.put("/chamas/{chama_id}/auto-contribution-settings")
async def update_auto_contribution_settings(
    chama_id: str,
    settings: AutoContributionSettings,
    current_user: dict = Depends(get_current_user)
):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update auto-contribution settings")

    # Get current chama to preserve last run timestamp
    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    # Validate contribution day
    if settings.contribution_day < 1 or settings.contribution_day > 31:
        raise HTTPException(status_code=400, detail="Contribution day must be between 1 and 31")

    # Validate amount
    if settings.contribution_amount < 0:
        raise HTTPException(status_code=400, detail="Contribution amount must be positive")

    # Validate fine amount
    if settings.fine_amount < 0:
        raise HTTPException(status_code=400, detail="Fine amount must be positive")

    # Update chama settings
    await db.chamas.update_one(
        {"_id": ObjectId(chama_id)},
        {"$set": {
            "auto_contribution_enabled": settings.enabled,
            "contribution_deadline": settings.contribution_day,
            "contribution_amount": settings.contribution_amount,
            "auto_create_contributions": settings.auto_create_contributions,
            "fine_amount": settings.fine_amount,
            "last_auto_contribution_run": chama.get("last_auto_contribution_run")
        }}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "action": "update_auto_contribution_settings",
        "details": f"Auto-contributions {'enabled' if settings.enabled else 'disabled'}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "message": "Auto-contribution settings updated successfully",
        "settings": {
            "enabled": settings.enabled,
            "contribution_day": settings.contribution_day,
            "contribution_amount": settings.contribution_amount
        }
    }

@api_router.post("/contributions/generate-monthly/{chama_id}")
async def generate_monthly_contributions(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can generate monthly contributions")

    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    # Check if auto-contribution is enabled
    if not chama.get("auto_contribution_enabled", False):
        raise HTTPException(status_code=400, detail="Auto-contribution is not enabled for this Chama")

    contribution_amount = chama.get("contribution_amount", 0)
    contribution_day = chama.get("contribution_deadline", 1)

    if contribution_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid contribution amount")

    # Get all active members
    members = await db.members.find({
        "chama_id": chama_id,
        "status": "active"
    }).to_list(1000)

    # Calculate next month's due date
    today = datetime.utcnow()
    next_month = today.month + 1 if today.month < 12 else 1
    next_year = today.year if today.month < 12 else today.year + 1

    # Ensure the day is valid for the month
    import calendar
    max_day = calendar.monthrange(next_year, next_month)[1]
    safe_day = min(contribution_day, max_day)

    due_date = datetime(next_year, next_month, safe_day).isoformat().split('T')[0]

    # Check if contributions already exist for this period
    existing_contributions = await db.contributions.find({
        "chama_id": chama_id,
        "due_date": due_date,
        "is_historical": {"$ne": True}
    }).to_list(10)

    if existing_contributions and len(existing_contributions) > 0:
        return {
            "message": "Contributions for this period already exist",
            "count": 0,
            "due_date": due_date
        }

    # Get fine amount
    fine_amount = chama.get("fine_amount", 0.0)

    # Create contributions for all members (one contribution per month - base + penalty only)
    contributions_to_insert = []
    members_with_penalties = 0

    for m in members:
        member_id = str(m["_id"])

        # Check for unpaid contributions for this member
        unpaid_contributions = await db.contributions.find({
            "chama_id": chama_id,
            "member_id": member_id,
            "status": {"$in": ["pending", "pending_verification"]},
            "due_date": {"$lt": due_date},  # Only past contributions
        }).to_list(1000)

        has_unpaid = unpaid_contributions and len(unpaid_contributions) > 0

        # Calculate amount for this month: base + penalty (if has unpaid)
        # Previous unpaid contributions remain separate and are NOT added to this amount
        if has_unpaid and fine_amount > 0:
            current_month_amount = contribution_amount + fine_amount
            total_unpaid = sum(c["amount"] for c in unpaid_contributions)
            members_with_penalties += 1
            penalty_info = {
                "has_penalty": True,
                "penalty_amount": fine_amount,
                "reason": "Late payment penalty for unpaid contributions",
                "unpaid_count": len(unpaid_contributions),
                "total_unpaid_balance": total_unpaid,
                "note": "Previous unpaid contributions remain separate and must be paid individually"
            }
        else:
            current_month_amount = contribution_amount
            penalty_info = {
                "has_penalty": False,
                "penalty_amount": 0
            }

        # Current month contribution - base amount + penalty ONLY (NO consolidation of old unpaid)
        contribution_dict = {
            "chama_id": chama_id,
            "member_id": member_id,
            "amount": current_month_amount,  # Base + penalty (if applicable), NOT including old unpaid
            "due_date": due_date,
            "status": "pending",
            "paid_date": None,
            "is_historical": False,
            "auto_generated": True,
            "created_at": datetime.utcnow().isoformat(),
            "penalty_info": penalty_info
        }

        contributions_to_insert.append(contribution_dict)

    if contributions_to_insert:
        result = await db.contributions.insert_many(contributions_to_insert)
        inserted_count = len(result.inserted_ids)

        # Update last run timestamp
        await db.chamas.update_one(
            {"_id": ObjectId(chama_id)},
            {"$set": {"last_auto_contribution_run": datetime.utcnow().isoformat()}}
        )

        # Audit log
        await db.audit_logs.insert_one({
            "chama_id": chama_id,
            "user_id": str(current_user["_id"]),
            "action": "generate_monthly_contributions",
            "details": f"Generated {inserted_count} contributions for {due_date} ({members_with_penalties} with penalties)",
            "timestamp": datetime.utcnow().isoformat()
        })

        return {
            "message": f"Successfully generated {inserted_count} monthly contributions",
            "count": inserted_count,
            "members_with_penalties": members_with_penalties,
            "due_date": due_date,
            "amount": contribution_amount,
            "penalty_amount": fine_amount
        }

    return {"message": "No contributions to generate", "count": 0}

@api_router.get("/contributions/preview-monthly/{chama_id}")
async def preview_monthly_contributions(chama_id: str, current_user: dict = Depends(get_current_user)):
    """Preview what will be generated without actually creating contributions"""
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can preview contributions")

    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    if not chama.get("auto_contribution_enabled", False):
        raise HTTPException(status_code=400, detail="Auto-contribution is not enabled for this Chama")

    contribution_amount = chama.get("contribution_amount", 0)
    contribution_day = chama.get("contribution_deadline", 1)

    if contribution_amount <= 0:
        raise HTTPException(status_code=400, detail="Invalid contribution amount")

    # Get all active members
    members = await db.members.find({
        "chama_id": chama_id,
        "status": "active"
    }).to_list(1000)

    # Calculate next month's due date
    today = datetime.utcnow()
    next_month = today.month + 1 if today.month < 12 else 1
    next_year = today.year if today.month < 12 else today.year + 1

    import calendar
    max_day = calendar.monthrange(next_year, next_month)[1]
    safe_day = min(contribution_day, max_day)

    due_date = datetime(next_year, next_month, safe_day).isoformat().split('T')[0]

    # Check if contributions already exist
    existing_contributions = await db.contributions.find({
        "chama_id": chama_id,
        "due_date": due_date,
        "is_historical": {"$ne": True}
    }).to_list(10)

    if existing_contributions and len(existing_contributions) > 0:
        return {
            "already_exists": True,
            "message": "Contributions for this period already exist",
            "due_date": due_date,
            "preview": []
        }

    fine_amount = chama.get("fine_amount", 0.0)

    # Build preview for each member
    preview_list = []
    total_regular = 0
    total_with_penalties = 0

    for m in members:
        member_id = str(m["_id"])

        # Get user details
        user = await db.users.find_one({"_id": ObjectId(m["user_id"])})
        member_name = user["name"] if user else "Unknown"

        # Check for unpaid contributions
        unpaid_contributions = await db.contributions.find({
            "chama_id": chama_id,
            "member_id": member_id,
            "status": {"$in": ["pending", "pending_verification"]},
            "due_date": {"$lt": due_date},
        }).to_list(1000)

        has_unpaid = unpaid_contributions and len(unpaid_contributions) > 0

        if has_unpaid and fine_amount > 0:
            total_unpaid = sum(c["amount"] for c in unpaid_contributions)

            preview_list.append({
                "member_id": member_id,
                "member_name": member_name,
                "has_arrears": True,
                "amount": contribution_amount + fine_amount,
                "breakdown": {
                    "current_month_only": contribution_amount,
                    "penalty_for_late_payments": fine_amount,
                    "existing_unpaid_balance": total_unpaid,
                    "unpaid_count": len(unpaid_contributions),
                    "note": "Previous unpaid contributions remain separate"
                }
            })
            total_with_penalties += 1
        else:
            preview_list.append({
                "member_id": member_id,
                "member_name": member_name,
                "has_arrears": False,
                "amount": contribution_amount,
                "breakdown": {
                    "current_contribution": contribution_amount,
                    "penalty": 0
                }
            })
            total_regular += 1

    return {
        "already_exists": False,
        "due_date": due_date,
        "total_members": len(members),
        "regular_contributions": total_regular,
        "arrears_contributions": total_with_penalties,
        "base_amount": contribution_amount,
        "fine_amount": fine_amount,
        "preview": preview_list
    }

@api_router.get("/chamas/{chama_id}/auto-contribution-status")
async def get_auto_contribution_status(chama_id: str, current_user: dict = Depends(get_current_user)):
    """Get detailed status and history of auto-contribution generation"""
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can view auto-contribution status")

    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    # Get last 10 auto-generation audit logs
    recent_generations = await db.audit_logs.find({
        "chama_id": chama_id,
        "action": {"$in": ["auto_generate_contributions", "generate_monthly_contributions"]}
    }).sort("timestamp", -1).limit(10).to_list(10)

    generation_history = []
    for log in recent_generations:
        generation_history.append({
            "timestamp": log["timestamp"],
            "action": log["action"],
            "details": log["details"],
            "user_id": log.get("user_id", "system")
        })

    # Count total auto-generated contributions
    total_auto_generated = await db.contributions.count_documents({
        "chama_id": chama_id,
        "auto_generated": True
    })

    return {
        "enabled": chama.get("auto_contribution_enabled", False),
        "contribution_day": chama.get("contribution_deadline", 1),
        "contribution_amount": chama.get("contribution_amount", 0),
        "fine_amount": chama.get("fine_amount", 0.0),
        "last_run": chama.get("last_auto_contribution_run"),
        "total_auto_generated": total_auto_generated,
        "recent_generations": generation_history
    }

# Loan Endpoints
@api_router.post("/loans/request")
async def request_loan(data: LoanRequest, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Validate loan parameters
    validation = validate_loan_params(data.amount, data.interest_rate, data.repayment_period)
    if not validation["is_valid"]:
        raise HTTPException(status_code=400, detail="; ".join(validation["errors"]))

    # Generate amortization schedule
    amortization = generate_amortization_schedule(
        principal=data.amount,
        annual_interest_rate=data.interest_rate,
        term_in_months=data.repayment_period,
        start_date=datetime.utcnow().isoformat(),
        payment_frequency=data.payment_frequency
    )

    amortization_dict = amortization.to_dict()

    # Calculate monthly payment
    monthly_payment = calculate_monthly_payment(
        data.amount,
        data.interest_rate,
        data.repayment_period
    )

    loan_dict = {
        "chama_id": data.chama_id,
        "member_id": str(member["_id"]),
        "user_id": str(current_user["_id"]),
        "amount": data.amount,
        "interest_rate": data.interest_rate,
        "interest_amount": round(amortization_dict["total_interest"], 2),
        "total_amount_due": round(amortization_dict["total_payment"], 2),
        "monthly_payment": round(monthly_payment, 2),
        "reason": data.reason,
        "repayment_period": data.repayment_period,
        "payment_frequency": data.payment_frequency,
        "number_of_installments": amortization_dict["number_of_payments"],
        "amortization_schedule": amortization_dict["schedule"],
        "status": "pending",
        "approved_date": None,
        "outstanding_balance": round(amortization_dict["total_payment"], 2),
        "next_payment_due": amortization_dict["schedule"][0]["due_date"] if amortization_dict["schedule"] else None,
        "paid_installments": [],
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.loans.insert_one(loan_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "request_loan",
        "details": f"Loan requested: KES {data.amount} for {data.repayment_period} months at {data.interest_rate}%",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "loan_id": str(result.inserted_id),
        "message": "Loan request submitted",
        "monthly_payment": round(monthly_payment, 2),
        "total_interest": round(amortization_dict["total_interest"], 2),
        "total_payable": round(amortization_dict["total_payment"], 2)
    }

@api_router.get("/loans/{chama_id}")
async def get_loans(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")
    
    loans = await db.loans.find({"chama_id": chama_id}).sort("created_at", -1).limit(200).to_list(200)
    
    result = []
    for loan in loans:
        user = await db.users.find_one({"_id": ObjectId(loan["user_id"])})
        result.append({
            "id": str(loan["_id"]),
            "member_name": user["name"] if user else "Unknown",
            "amount": loan["amount"],
            "interest_rate": loan["interest_rate"],
            "interest_amount": loan.get("interest_amount", 0),
            "total_amount_due": loan.get("total_amount_due", loan["amount"]),
            "reason": loan["reason"],
            "status": loan["status"],
            "outstanding_balance": loan.get("outstanding_balance", loan.get("total_amount_due", loan["amount"])),
            "repayment_period": loan.get("repayment_period", 0),
            "created_at": loan["created_at"]
        })

    return result

@api_router.put("/loans/approve")
async def approve_loan(data: LoanApproval, current_user: dict = Depends(get_current_user)):
    loan = await db.loans.find_one({"_id": ObjectId(data.loan_id)})
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")
    
    # Verify admin
    member = await db.members.find_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can approve loans")
    
    update_data = {"status": data.status}
    if data.status == "approved":
        update_data["approved_date"] = datetime.utcnow().isoformat()
    
    await db.loans.update_one(
        {"_id": ObjectId(data.loan_id)},
        {"$set": update_data}
    )
    
    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "loan_approval",
        "details": f"Loan {data.status}",
        "timestamp": datetime.utcnow().isoformat()
    })
    
    return {"message": f"Loan {data.status} successfully"}

@api_router.post("/loans/repay")
async def repay_loan(data: RepaymentCreate, current_user: dict = Depends(get_current_user)):
    loan = await db.loans.find_one({"_id": ObjectId(data.loan_id)})
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Verify loan is approved
    if loan["status"] != "approved":
        raise HTTPException(status_code=400, detail="Can only repay approved loans")

    # Verify admin or loan owner
    member = await db.members.find_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not authorized")

    # Calculate payment breakdown (principal vs interest)
    outstanding_balance = loan.get("outstanding_balance", loan.get("total_amount_due", loan["amount"]))
    monthly_payment = loan.get("monthly_payment", 0)

    breakdown = calculate_payment_breakdown(
        remaining_balance=outstanding_balance,
        annual_interest_rate=loan.get("interest_rate", 0),
        regular_payment=data.amount
    )

    # Determine next installment to pay from schedule
    paid_installments = loan.get("paid_installments", [])
    amortization_schedule = loan.get("amortization_schedule", [])

    next_installment_index = len(paid_installments)
    next_installment = None

    if amortization_schedule and next_installment_index < len(amortization_schedule):
        next_installment = amortization_schedule[next_installment_index]

    # Create repayment record
    repayment_dict = {
        "loan_id": data.loan_id,
        "amount": data.amount,
        "principal_amount": round(breakdown["principal"], 2),
        "interest_amount": round(breakdown["interest"], 2),
        "installment_number": next_installment_index + 1 if next_installment else None,
        "scheduled_date": next_installment["due_date"] if next_installment else None,
        "payment_date": datetime.utcnow().isoformat(),
        "confirmed_by": str(current_user["_id"]),
        "balance_after_payment": max(0, outstanding_balance - data.amount),
        "created_at": datetime.utcnow().isoformat()
    }
    await db.repayments.insert_one(repayment_dict)

    # Update loan balance and paid installments
    new_balance = max(0, outstanding_balance - data.amount)
    update_data = {
        "outstanding_balance": round(new_balance, 2),
        "last_payment_date": datetime.utcnow().isoformat()
    }

    # Mark installment as paid if it matches
    if next_installment:
        paid_installments.append({
            "installment_number": next_installment_index + 1,
            "due_date": next_installment["due_date"],
            "paid_date": datetime.utcnow().isoformat(),
            "amount_paid": data.amount,
            "principal": breakdown["principal"],
            "interest": breakdown["interest"]
        })
        update_data["paid_installments"] = paid_installments

        # Update next payment due date
        if next_installment_index + 1 < len(amortization_schedule):
            update_data["next_payment_due"] = amortization_schedule[next_installment_index + 1]["due_date"]
        else:
            update_data["next_payment_due"] = None

    # Mark loan as fully paid if balance is zero
    if new_balance < 0.01:  # Account for rounding
        update_data["status"] = "fully_paid"
        update_data["paid_date"] = datetime.utcnow().isoformat()

    await db.loans.update_one(
        {"_id": ObjectId(data.loan_id)},
        {"$set": update_data}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "loan_repayment",
        "details": f"Repayment: KES {data.amount} (Principal: {breakdown['principal']}, Interest: {breakdown['interest']})",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "message": "Repayment recorded successfully",
        "remaining_balance": round(new_balance, 2),
        "principal_paid": round(breakdown["principal"], 2),
        "interest_paid": round(breakdown["interest"], 2),
        "installments_paid": len(paid_installments),
        "installments_remaining": len(amortization_schedule) - len(paid_installments) if amortization_schedule else 0,
        "next_payment_due": update_data.get("next_payment_due"),
        "fully_paid": new_balance < 0.01
    }

# Loan Calculator Endpoints
@api_router.get("/loans/{loan_id}/amortization-schedule")
async def get_loan_amortization_schedule(loan_id: str, current_user: dict = Depends(get_current_user)):
    """Get detailed amortization schedule for a specific loan"""
    loan = await db.loans.find_one({"_id": ObjectId(loan_id)})
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get user details
    user = await db.users.find_one({"_id": ObjectId(loan["user_id"])})

    # Get repayment history
    repayments = await db.repayments.find({"loan_id": loan_id}).to_list(1000)

    return {
        "loan_id": loan_id,
        "borrower_name": user["name"] if user else "Unknown",
        "principal": loan["amount"],
        "interest_rate": loan["interest_rate"],
        "term_months": loan["repayment_period"],
        "monthly_payment": loan.get("monthly_payment", 0),
        "total_interest": loan.get("interest_amount", 0),
        "total_payable": loan.get("total_amount_due", 0),
        "outstanding_balance": loan.get("outstanding_balance", 0),
        "status": loan["status"],
        "amortization_schedule": loan.get("amortization_schedule", []),
        "paid_installments": loan.get("paid_installments", []),
        "next_payment_due": loan.get("next_payment_due"),
        "payment_frequency": loan.get("payment_frequency", "monthly"),
        "repayment_history": [
            {
                "payment_date": r["payment_date"],
                "amount": r["amount"],
                "principal": r.get("principal_amount", 0),
                "interest": r.get("interest_amount", 0),
                "balance_after": r.get("balance_after_payment", 0)
            }
            for r in repayments
        ]
    }

@api_router.post("/loans/calculate")
async def calculate_loan(
    principal: float,
    interest_rate: float,
    term_months: int,
    payment_frequency: str = "monthly"
):
    """Calculate loan details without creating a loan request"""
    # Validate parameters
    validation = validate_loan_params(principal, interest_rate, term_months)
    if not validation["is_valid"]:
        raise HTTPException(status_code=400, detail="; ".join(validation["errors"]))

    # Generate amortization
    amortization = generate_amortization_schedule(
        principal=principal,
        annual_interest_rate=interest_rate,
        term_in_months=term_months,
        payment_frequency=payment_frequency
    )

    monthly_payment = calculate_monthly_payment(principal, interest_rate, term_months)

    return {
        "principal": principal,
        "interest_rate": interest_rate,
        "term_months": term_months,
        "payment_frequency": payment_frequency,
        "monthly_payment": round(monthly_payment, 2),
        "total_interest": round(amortization.total_interest, 2),
        "total_payable": round(amortization.total_payment, 2),
        "number_of_payments": amortization.number_of_payments,
        "schedule": [s.to_dict() for s in amortization.schedule]
    }

@api_router.post("/loans/affordability-check")
async def check_loan_affordability(
    monthly_income: float,
    existing_debts: float,
    loan_amount: float,
    interest_rate: float,
    term_months: int
):
    """Check if a proposed loan is affordable based on income"""
    # Calculate monthly payment
    monthly_payment = calculate_monthly_payment(loan_amount, interest_rate, term_months)

    # Check affordability
    affordability_result = calculate_affordability(
        monthly_income=monthly_income,
        existing_monthly_debts=existing_debts,
        proposed_loan_payment=monthly_payment
    )

    return {
        "monthly_payment": round(monthly_payment, 2),
        "monthly_income": monthly_income,
        "existing_debts": existing_debts,
        "current_dti": affordability_result["current_dti"],
        "new_dti": affordability_result["new_dti"],
        "is_affordable": affordability_result["is_affordable"],
        "max_affordable_payment": affordability_result["max_affordable_payment"],
        "recommendation": (
            "This loan is affordable and within recommended debt limits."
            if affordability_result["is_affordable"]
            else f"This loan would exceed the recommended 36% debt-to-income ratio. "
                 f"Consider reducing the loan amount or extending the term."
        )
    }

@api_router.post("/loans/max-loan-calculator")
async def calculate_max_loan(
    monthly_payment_capacity: float,
    interest_rate: float,
    term_months: int
):
    """Calculate maximum affordable loan based on payment capacity"""
    from loan_calculator import calculate_max_loan_amount

    max_loan = calculate_max_loan_amount(
        monthly_payment_capacity=monthly_payment_capacity,
        annual_interest_rate=interest_rate,
        term_in_months=term_months
    )

    total_interest = max_loan * (interest_rate / 100) * (term_months / 12)

    return {
        "max_loan_amount": round(max_loan, 2),
        "monthly_payment": monthly_payment_capacity,
        "interest_rate": interest_rate,
        "term_months": term_months,
        "total_interest": round(total_interest, 2),
        "message": f"Based on a monthly payment capacity of KES {monthly_payment_capacity:,.2f}, "
                   f"you can borrow up to KES {max_loan:,.2f} over {term_months} months at {interest_rate}% interest."
    }

@api_router.get("/loans/{loan_id}/payment-schedule-status")
async def get_payment_schedule_status(loan_id: str, current_user: dict = Depends(get_current_user)):
    """Get payment schedule with status (paid, overdue, upcoming)"""
    loan = await db.loans.find_one({"_id": ObjectId(loan_id)})
    if not loan:
        raise HTTPException(status_code=404, detail="Loan not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    schedule = loan.get("amortization_schedule", [])
    paid_installments = loan.get("paid_installments", [])
    paid_numbers = {inst["installment_number"] for inst in paid_installments}

    today = datetime.utcnow().date()

    enriched_schedule = []
    for installment in schedule:
        payment_num = installment["payment_number"]
        due_date = datetime.fromisoformat(installment["due_date"]).date()

        # Determine status
        if payment_num in paid_numbers:
            status = "paid"
            paid_inst = next((p for p in paid_installments if p["installment_number"] == payment_num), None)
            paid_date = paid_inst["paid_date"] if paid_inst else None
        elif due_date < today:
            status = "overdue"
            paid_date = None
        elif due_date == today:
            status = "due_today"
            paid_date = None
        else:
            status = "upcoming"
            paid_date = None

        enriched_schedule.append({
            **installment,
            "status": status,
            "paid_date": paid_date,
            "days_overdue": (today - due_date).days if status == "overdue" else 0
        })

    # Calculate summary stats
    paid_count = len(paid_installments)
    overdue_count = sum(1 for s in enriched_schedule if s["status"] == "overdue")
    upcoming_count = sum(1 for s in enriched_schedule if s["status"] == "upcoming")

    return {
        "loan_id": loan_id,
        "schedule": enriched_schedule,
        "summary": {
            "total_installments": len(schedule),
            "paid_installments": paid_count,
            "overdue_installments": overdue_count,
            "upcoming_installments": upcoming_count,
            "completion_rate": round((paid_count / len(schedule) * 100) if schedule else 0, 1)
        }
    }

# Dashboard & Reports
@api_router.get("/dashboard/{chama_id}")
async def get_dashboard(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")
    
    # Calculate totals
    contributions = await db.contributions.find({"chama_id": chama_id}).to_list(10000)
    total_contributions = sum(c["amount"] for c in contributions if c["status"] == "paid")
    pending_contributions = sum(c["amount"] for c in contributions if c["status"] == "pending")
    
    loans = await db.loans.find({"chama_id": chama_id, "status": "approved"}).to_list(10000)
    total_loans_issued = sum(l["amount"] for l in loans)

    # Optimized: Filter repayments by loan_ids directly in MongoDB
    loan_ids = [str(l["_id"]) for l in loans]
    repayments = await db.repayments.find({"loan_id": {"$in": loan_ids}}).to_list(10000) if loan_ids else []
    total_repaid = sum(r["amount"] for r in repayments)
    
    # Get total merry-go-round disbursements (balance-funded only)
    mgr_disbursements = await db.merry_go_round_disbursements.find({"chama_id": chama_id}).to_list(10000)
    total_mgr_disbursements = sum(d["amount"] for d in mgr_disbursements)

    outstanding_loans = total_loans_issued - total_repaid
    current_balance = total_contributions - total_loans_issued + total_repaid - total_mgr_disbursements

    member_count = await db.members.count_documents({"chama_id": chama_id, "status": "active"})
    
    return {
        "total_contributions": total_contributions,
        "pending_contributions": pending_contributions,
        "total_loans_issued": total_loans_issued,
        "total_repaid": total_repaid,
        "outstanding_loans": outstanding_loans,
        "current_balance": current_balance,
        "member_count": member_count
    }

# Reports & Analytics Endpoints
@api_router.get("/reports/analytics/{chama_id}")
async def get_reports_analytics(
    chama_id: str,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    current_user: dict = Depends(get_current_user)
):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Build date filter if provided
    date_filter = {}
    if start_date or end_date:
        date_filter["paid_date"] = {}
        if start_date:
            date_filter["paid_date"]["$gte"] = start_date
        if end_date:
            date_filter["paid_date"]["$lte"] = end_date

    # Get all data for analytics
    contribution_filter = {"chama_id": chama_id}
    if date_filter:
        contribution_filter.update(date_filter)

    # Use aggregation instead of loading all documents
    contribution_pipeline = [
        {"$match": contribution_filter},
        {"$group": {
            "_id": "$status",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1}
        }}
    ]
    contribution_stats = await db.contributions.aggregate(contribution_pipeline).to_list(10)
    contributions = []  # Don't load all contributions

    # For loans, use aggregation
    loan_pipeline = [
        {"$match": {"chama_id": chama_id}},
        {"$group": {
            "_id": "$status",
            "total": {"$sum": "$amount"},
            "count": {"$sum": 1}
        }}
    ]
    loan_stats = await db.loans.aggregate(loan_pipeline).to_list(10)
    loans = []  # Don't load all loans

    # Get member count only
    member_count = await db.members.count_documents({"chama_id": chama_id, "status": "active"})
    members_list = []  # Don't load all members

    # Calculate key metrics
    total_contributions = sum(c["amount"] for c in contributions if c["status"] == "paid")
    pending_contributions = sum(c["amount"] for c in contributions if c["status"] in ["pending", "pending_verification"])

    approved_loans = [l for l in loans if l["status"] == "approved"]
    total_loans_issued = sum(l["amount"] for l in approved_loans)

    # Optimized: Filter repayments by loan_ids directly in MongoDB
    loan_ids = [str(l["_id"]) for l in approved_loans]
    repayments = await db.repayments.find({"loan_id": {"$in": loan_ids}}).to_list(10000) if loan_ids else []
    total_repaid = sum(r["amount"] for r in repayments)
    outstanding_loans = total_loans_issued - total_repaid

    # Calculate rates
    total_expected = total_contributions + pending_contributions
    contribution_rate = (total_contributions / total_expected * 100) if total_expected > 0 else 0
    default_rate = (outstanding_loans / total_loans_issued * 100) if total_loans_issued > 0 else 0

    # Member participation
    active_members = len([m for m in members_list])
    contributing_members = len(set([c["member_id"] for c in contributions if c["status"] == "paid"]))

    # Monthly growth - compare last 2 months
    now = datetime.utcnow()
    current_month_start = datetime(now.year, now.month, 1).isoformat()

    if now.month == 1:
        prev_month_start = datetime(now.year - 1, 12, 1).isoformat()
        prev_month_end = datetime(now.year, 1, 1).isoformat()
    else:
        prev_month_start = datetime(now.year, now.month - 1, 1).isoformat()
        prev_month_end = current_month_start

    current_month_contrib = sum(c["amount"] for c in contributions
                                if c["status"] == "paid" and c.get("paid_date", "") >= current_month_start)
    prev_month_contrib = sum(c["amount"] for c in contributions
                            if c["status"] == "paid"
                            and c.get("paid_date", "") >= prev_month_start
                            and c.get("paid_date", "") < prev_month_end)

    monthly_growth = ((current_month_contrib - prev_month_contrib) / prev_month_contrib * 100) if prev_month_contrib > 0 else 0

    # Contribution trends (last 6 months)
    trends = []
    for i in range(5, -1, -1):
        if now.month - i > 0:
            month = now.month - i
            year = now.year
        else:
            month = 12 + (now.month - i)
            year = now.year - 1

        month_start = datetime(year, month, 1).isoformat()
        if month == 12:
            month_end = datetime(year + 1, 1, 1).isoformat()
        else:
            month_end = datetime(year, month + 1, 1).isoformat()

        month_total = sum(c["amount"] for c in contributions
                         if c["status"] == "paid"
                         and c.get("paid_date", "") >= month_start
                         and c.get("paid_date", "") < month_end)

        month_name = calendar.month_abbr[month]
        trends.append({"month": month_name, "amount": month_total})

    # Loan status distribution
    pending_loans = len([l for l in loans if l["status"] == "pending"])
    approved_loan_count = len(approved_loans)
    rejected_loans = len([l for l in loans if l["status"] == "rejected"])

    # Advanced SACCO KPIs
    current_balance = total_contributions - total_loans_issued + total_repaid

    # Loan-to-Share Ratio (should be < 100% for healthy SACCO)
    loan_to_share_ratio = (total_loans_issued / total_contributions * 100) if total_contributions > 0 else 0

    # Portfolio at Risk (PAR) - percentage of outstanding loans
    portfolio_at_risk = (outstanding_loans / total_loans_issued * 100) if total_loans_issued > 0 else 0

    # Return on Assets (ROA) - simplified: interest earned / total assets
    total_interest_earned = sum(l["amount"] * (l.get("interest_rate", 0) / 100) for l in approved_loans)
    total_assets = current_balance + outstanding_loans
    return_on_assets = (total_interest_earned / total_assets * 100) if total_assets > 0 else 0

    # Operating Efficiency Ratio - contributions per active member
    avg_contribution_per_member = total_contributions / active_members if active_members > 0 else 0

    # Capital Adequacy - current balance as % of total loans
    capital_adequacy_ratio = (current_balance / total_loans_issued * 100) if total_loans_issued > 0 else 100

    # Member Growth Rate (year-over-year) - simplified: current members
    member_growth_rate = 0  # Placeholder - would need historical data

    # Loan Approval Rate
    total_loan_applications = len(loans)
    loan_approval_rate = (approved_loan_count / total_loan_applications * 100) if total_loan_applications > 0 else 0

    return {
        "key_metrics": {
            "contribution_rate": round(contribution_rate, 1),
            "default_rate": round(default_rate, 1),
            "active_members": active_members,
            "contributing_members": contributing_members,
            "monthly_growth": round(monthly_growth, 1)
        },
        "advanced_metrics": {
            "loan_to_share_ratio": round(loan_to_share_ratio, 1),
            "portfolio_at_risk": round(portfolio_at_risk, 1),
            "return_on_assets": round(return_on_assets, 2),
            "avg_contribution_per_member": round(avg_contribution_per_member, 2),
            "capital_adequacy_ratio": round(capital_adequacy_ratio, 1),
            "loan_approval_rate": round(loan_approval_rate, 1),
            "total_interest_earned": round(total_interest_earned, 2)
        },
        "financial_summary": {
            "total_contributions": total_contributions,
            "pending_contributions": pending_contributions,
            "total_loans_issued": total_loans_issued,
            "outstanding_loans": outstanding_loans,
            "total_repaid": total_repaid,
            "current_balance": current_balance
        },
        "contribution_trends": trends,
        "loan_distribution": {
            "pending": pending_loans,
            "approved": approved_loan_count,
            "rejected": rejected_loans
        }
    }

@api_router.get("/reports/member-statement/{chama_id}/{member_id}")
async def get_member_statement(chama_id: str, member_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    current_member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not current_member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Admin can view all, members can only view their own
    if current_member["role"] != "admin" and str(current_member["_id"]) != member_id:
        raise HTTPException(status_code=403, detail="You can only view your own statement")

    # Get member details
    target_member = await db.members.find_one({"_id": ObjectId(member_id)})
    if not target_member or target_member["chama_id"] != chama_id:
        raise HTTPException(status_code=404, detail="Member not found")

    user = await db.users.find_one({"_id": ObjectId(target_member["user_id"])})

    # Get contributions
    contributions = await db.contributions.find({
        "chama_id": chama_id,
        "member_id": member_id
    }).sort("due_date", -1).to_list(1000)

    contributions_list = []
    for c in contributions:
        contributions_list.append({
            "id": str(c["_id"]),
            "amount": c["amount"],
            "due_date": c["due_date"],
            "paid_date": c.get("paid_date"),
            "status": c["status"],
            "is_arrears": c.get("is_arrears", False)
        })

    # Get loans
    loans = await db.loans.find({
        "chama_id": chama_id,
        "member_id": member_id
    }).sort("created_at", -1).to_list(1000)

    loans_list = []
    for l in loans:
        repayments = await db.repayments.find({"loan_id": str(l["_id"])}).to_list(1000)
        total_repaid = sum(r["amount"] for r in repayments)

        loans_list.append({
            "id": str(l["_id"]),
            "amount": l["amount"],
            "status": l["status"],
            "interest_rate": l["interest_rate"],
            "outstanding_balance": l.get("outstanding_balance", l["amount"]),
            "total_repaid": total_repaid,
            "created_at": l["created_at"],
            "approved_date": l.get("approved_date")
        })

    # Calculate totals
    total_contributed = sum(c["amount"] for c in contributions if c["status"] == "paid")
    total_pending = sum(c["amount"] for c in contributions if c["status"] in ["pending", "pending_verification"])
    total_borrowed = sum(l["amount"] for l in loans if l["status"] == "approved")
    # Fix: Calculate total repaid correctly from the loans_list we already built
    total_loan_repaid = sum(loan["total_repaid"] for loan in loans_list if loan["status"] == "approved")

    return {
        "member": {
            "id": str(target_member["_id"]),
            "name": user["name"],
            "phone": user["phone"],
            "role": target_member["role"],
            "joined_at": target_member["joined_at"]
        },
        "summary": {
            "total_contributed": total_contributed,
            "total_pending": total_pending,
            "total_borrowed": total_borrowed,
            "total_loan_repaid": total_loan_repaid,
            "net_position": total_contributed - total_borrowed + total_loan_repaid
        },
        "contributions": contributions_list,
        "loans": loans_list
    }

@api_router.get("/reports/contribution-summary/{chama_id}")
async def get_contribution_summary(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get all members and their contributions
    members_list = await db.members.find({"chama_id": chama_id, "status": "active"}).to_list(1000)
    contributions = await db.contributions.find({"chama_id": chama_id}).to_list(10000)

    summary = []
    for m in members_list:
        user = await db.users.find_one({"_id": ObjectId(m["user_id"])})
        member_id = str(m["_id"])

        member_contributions = [c for c in contributions if c["member_id"] == member_id]

        total_paid = sum(c["amount"] for c in member_contributions if c["status"] == "paid")
        total_pending = sum(c["amount"] for c in member_contributions if c["status"] in ["pending", "pending_verification"])
        total_expected = total_paid + total_pending

        compliance_rate = (total_paid / total_expected * 100) if total_expected > 0 else 0

        summary.append({
            "member_id": member_id,
            "name": user["name"] if user else "Unknown",
            "total_paid": total_paid,
            "total_pending": total_pending,
            "total_expected": total_expected,
            "compliance_rate": round(compliance_rate, 1),
            "arrears": total_pending
        })

    # Sort by compliance rate descending
    summary.sort(key=lambda x: x["compliance_rate"], reverse=True)

    return {
        "members": summary,
        "totals": {
            "total_collected": sum(s["total_paid"] for s in summary),
            "total_pending": sum(s["total_pending"] for s in summary),
            "average_compliance": round(sum(s["compliance_rate"] for s in summary) / len(summary), 1) if summary else 0
        }
    }

@api_router.get("/reports/loan-portfolio/{chama_id}")
async def get_loan_portfolio(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    loans = await db.loans.find({"chama_id": chama_id}).to_list(10000)

    portfolio = []
    for l in loans:
        # Get member details
        loan_member = await db.members.find_one({"_id": ObjectId(l["member_id"])})
        member_name = "Unknown"
        if loan_member:
            user = await db.users.find_one({"_id": ObjectId(loan_member["user_id"])})
            member_name = user["name"] if user else "Unknown"

        # Get repayments
        repayments = await db.repayments.find({"loan_id": str(l["_id"])}).to_list(1000)
        total_repaid = sum(r["amount"] for r in repayments)

        repayment_rate = (total_repaid / l["amount"] * 100) if l["amount"] > 0 else 0

        portfolio.append({
            "loan_id": str(l["_id"]),
            "member_name": member_name,
            "amount": l["amount"],
            "interest_rate": l["interest_rate"],
            "status": l["status"],
            "outstanding_balance": l.get("outstanding_balance", l["amount"]),
            "total_repaid": total_repaid,
            "repayment_rate": round(repayment_rate, 1),
            "created_at": l["created_at"],
            "approved_date": l.get("approved_date")
        })

    # Calculate totals
    approved_loans = [l for l in portfolio if l["status"] == "approved"]
    total_issued = sum(l["amount"] for l in approved_loans)
    total_repaid_all = sum(l["total_repaid"] for l in approved_loans)
    total_outstanding = sum(l["outstanding_balance"] for l in approved_loans)

    return {
        "loans": portfolio,
        "summary": {
            "total_loans": len(loans),
            "approved_loans": len(approved_loans),
            "pending_loans": len([l for l in loans if l["status"] == "pending"]),
            "total_issued": total_issued,
            "total_repaid": total_repaid_all,
            "total_outstanding": total_outstanding,
            "portfolio_at_risk": round((total_outstanding / total_issued * 100) if total_issued > 0 else 0, 1)
        }
    }

@api_router.get("/reports/financial-statement/{chama_id}")
async def get_financial_statement(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get all financial data
    contributions = await db.contributions.find({"chama_id": chama_id}).to_list(10000)
    loans = await db.loans.find({"chama_id": chama_id, "status": "approved"}).to_list(10000)

    # Optimized: Filter repayments by loan_ids directly in MongoDB
    loan_ids = [str(l["_id"]) for l in loans]
    repayments = await db.repayments.find({"loan_id": {"$in": loan_ids}}).to_list(10000) if loan_ids else []

    # Assets
    cash_from_contributions = sum(c["amount"] for c in contributions if c["status"] == "paid")
    cash_from_repayments = sum(r["amount"] for r in repayments)
    total_cash = cash_from_contributions + cash_from_repayments

    loans_receivable = sum(l.get("outstanding_balance", l["amount"]) for l in loans)
    total_assets = total_cash + loans_receivable - sum(l["amount"] for l in loans)  # Adjust for loans issued

    # Liabilities (member equity/contributions)
    member_equity = cash_from_contributions
    retained_earnings = cash_from_repayments - cash_from_contributions  # Interest earned

    total_equity = member_equity + retained_earnings

    # Income Statement - use actual interest amounts from approved/completed loans
    interest_income = sum(l.get("interest_amount", 0) for l in loans if l["status"] in ["approved", "rejected"])
    total_income = interest_income

    # Expenses (simplified - could include admin costs, etc.)
    operating_expenses = 0  # Placeholder
    net_income = total_income - operating_expenses

    # Cash Flow
    cash_from_operations = cash_from_contributions
    cash_from_financing = 0
    cash_from_investing = -sum(l["amount"] for l in loans) + cash_from_repayments
    net_cash_flow = cash_from_operations + cash_from_financing + cash_from_investing

    return {
        "balance_sheet": {
            "assets": {
                "current_assets": {
                    "cash": total_cash - sum(l["amount"] for l in loans) + cash_from_repayments,
                    "loans_receivable": loans_receivable
                },
                "total_assets": total_assets + loans_receivable
            },
            "equity": {
                "member_contributions": member_equity,
                "retained_earnings": retained_earnings,
                "total_equity": total_equity + retained_earnings
            }
        },
        "income_statement": {
            "revenue": {
                "interest_income": interest_income,
                "total_revenue": total_income
            },
            "expenses": {
                "operating_expenses": operating_expenses,
                "total_expenses": operating_expenses
            },
            "net_income": net_income
        },
        "cash_flow": {
            "operating_activities": cash_from_operations,
            "investing_activities": cash_from_investing,
            "financing_activities": cash_from_financing,
            "net_cash_flow": net_cash_flow
        }
    }

# Announcements
@api_router.post("/announcements/create")
async def create_announcement(data: AnnouncementCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create announcements")
    
    announcement_dict = {
        "chama_id": data.chama_id,
        "title": data.title,
        "message": data.message,
        "created_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }
    
    result = await db.announcements.insert_one(announcement_dict)
    return {"announcement_id": str(result.inserted_id)}

@api_router.get("/announcements/{chama_id}")
async def get_announcements(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")
    
    announcements = await db.announcements.find({"chama_id": chama_id}).sort("created_at", -1).to_list(100)
    
    result = []
    for a in announcements:
        creator = await db.users.find_one({"_id": ObjectId(a["created_by"])})
        result.append({
            "id": str(a["_id"]),
            "title": a["title"],
            "message": a["message"],
            "created_by": creator["name"] if creator else "Unknown",
            "created_at": a["created_at"]
        })
    
    return result

# Merry-Go-Round Endpoints
@api_router.post("/merry-go-round/create")
async def create_merry_go_round(data: MerryGoRoundCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create merry-go-rounds")

    # Verify all members in order exist and belong to chama
    for member_id in data.member_order:
        m = await db.members.find_one({
            "_id": ObjectId(member_id),
            "chama_id": data.chama_id,
            "status": "active"
        })
        if not m:
            raise HTTPException(status_code=400, detail=f"Invalid member in rotation order: {member_id}")

    # Validate funding_source
    if data.funding_source not in ["contribution", "balance"]:
        raise HTTPException(status_code=400, detail="Invalid funding_source. Must be 'contribution' or 'balance'")

    # Create merry-go-round
    mgr_dict = {
        "chama_id": data.chama_id,
        "name": data.name,
        "contribution_amount": data.contribution_amount,
        "frequency": data.frequency,
        "member_order": data.member_order,
        "current_position": 0,
        "start_date": data.start_date,
        "funding_source": data.funding_source,
        "description": data.description,
        "status": "active",
        "created_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat(),
        "history": []
    }

    result = await db.merry_go_rounds.insert_one(mgr_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "create_merry_go_round",
        "details": f"Created Merry-Go-Round: {data.name}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"round_id": str(result.inserted_id), "message": "Merry-Go-Round created successfully"}

@api_router.get("/merry-go-round/{chama_id}")
async def get_merry_go_rounds(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    rounds = await db.merry_go_rounds.find({"chama_id": chama_id}).to_list(100)

    result = []
    for r in rounds:
        # Calculate total amount (contribution * number of members)
        total_amount = r["contribution_amount"] * len(r["member_order"])

        # Get next payout member
        current_pos = r.get("current_position", 0)
        next_member_id = None
        next_member_name = "N/A"

        if current_pos < len(r["member_order"]):
            next_member_id = r["member_order"][current_pos]
            next_member_doc = await db.members.find_one({"_id": ObjectId(next_member_id)})
            if next_member_doc:
                next_user = await db.users.find_one({"_id": ObjectId(next_member_doc["user_id"])})
                next_member_name = next_user["name"] if next_user else "Unknown"

        # Find current user's position in rotation
        user_member_id = str(member["_id"])
        user_position = -1
        if user_member_id in r["member_order"]:
            user_position = r["member_order"].index(user_member_id)

        # Calculate expected payout date based on frequency and position
        start_date = datetime.fromisoformat(r["start_date"])
        if r["frequency"] == "weekly":
            if user_position >= 0:
                expected_date = start_date + timedelta(weeks=user_position)
                next_payout_date = start_date + timedelta(weeks=current_pos)
            else:
                expected_date = None
                next_payout_date = start_date + timedelta(weeks=current_pos)
        else:  # monthly
            if user_position >= 0:
                # Add months
                month = start_date.month + user_position
                year = start_date.year + (month - 1) // 12
                month = ((month - 1) % 12) + 1
                expected_date = datetime(year, month, start_date.day)

                # Next payout date
                month_next = start_date.month + current_pos
                year_next = start_date.year + (month_next - 1) // 12
                month_next = ((month_next - 1) % 12) + 1
                next_payout_date = datetime(year_next, month_next, start_date.day)
            else:
                expected_date = None
                month_next = start_date.month + current_pos
                year_next = start_date.year + (month_next - 1) // 12
                month_next = ((month_next - 1) % 12) + 1
                next_payout_date = datetime(year_next, month_next, start_date.day)

        result.append({
            "id": str(r["_id"]),
            "name": r["name"],
            "total_amount": total_amount,
            "contribution_amount": r["contribution_amount"],
            "current_position": current_pos,
            "total_members": len(r["member_order"]),
            "next_payout": {
                "member": next_member_name,
                "date": next_payout_date.strftime("%b %d, %Y") if next_payout_date else "N/A",
                "amount": total_amount
            },
            "your_position": user_position + 1 if user_position >= 0 else -1,
            "your_expected_date": expected_date.strftime("%b %d, %Y") if expected_date else "N/A",
            "status": r["status"],
            "frequency": r["frequency"],
            "start_date": r["start_date"],
            "funding_source": r.get("funding_source", "contribution")
        })

    return result

@api_router.get("/merry-go-round/details/{round_id}")
async def get_merry_go_round_details(round_id: str, current_user: dict = Depends(get_current_user)):
    mgr = await db.merry_go_rounds.find_one({"_id": ObjectId(round_id)})
    if not mgr:
        raise HTTPException(status_code=404, detail="Merry-Go-Round not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    return {
        "id": str(mgr["_id"]),
        "name": mgr["name"],
        "contribution_amount": mgr["contribution_amount"],
        "frequency": mgr["frequency"],
        "member_order": mgr["member_order"],
        "current_position": mgr.get("current_position", 0),
        "start_date": mgr["start_date"],
        "funding_source": mgr.get("funding_source", "contribution"),
        "description": mgr.get("description"),
        "status": mgr["status"],
        "created_at": mgr["created_at"]
    }

@api_router.get("/merry-go-round/schedule/{round_id}")
async def get_merry_go_round_schedule(round_id: str, current_user: dict = Depends(get_current_user)):
    mgr = await db.merry_go_rounds.find_one({"_id": ObjectId(round_id)})
    if not mgr:
        raise HTTPException(status_code=404, detail="Merry-Go-Round not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Build schedule
    schedule = []
    start_date = datetime.fromisoformat(mgr["start_date"])

    for idx, member_id in enumerate(mgr["member_order"]):
        # Get member details
        member_doc = await db.members.find_one({"_id": ObjectId(member_id)})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        # Calculate payout date
        if mgr["frequency"] == "weekly":
            payout_date = start_date + timedelta(weeks=idx)
        else:  # monthly
            month = start_date.month + idx
            year = start_date.year + (month - 1) // 12
            month = ((month - 1) % 12) + 1
            payout_date = datetime(year, month, start_date.day)

        # Determine status
        current_pos = mgr.get("current_position", 0)
        if idx < current_pos:
            status = "completed"
        elif idx == current_pos:
            status = "current"
        else:
            status = "upcoming"

        schedule.append({
            "position": idx + 1,
            "member_id": member_id,
            "member_name": member_name,
            "payout_date": payout_date.strftime("%Y-%m-%d"),
            "payout_date_formatted": payout_date.strftime("%b %d, %Y"),
            "amount": mgr["contribution_amount"] * len(mgr["member_order"]),
            "status": status
        })

    return schedule

@api_router.get("/merry-go-round/history/{round_id}")
async def get_merry_go_round_history(round_id: str, current_user: dict = Depends(get_current_user)):
    mgr = await db.merry_go_rounds.find_one({"_id": ObjectId(round_id)})
    if not mgr:
        raise HTTPException(status_code=404, detail="Merry-Go-Round not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get history with member names
    history = mgr.get("history", [])
    result = []

    for h in history:
        member_doc = await db.members.find_one({"_id": ObjectId(h["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        result.append({
            "member_id": h["member_id"],
            "member_name": member_name,
            "position": h["position"],
            "amount": h["amount"],
            "payout_date": h["payout_date"],
            "payment_method": h.get("payment_method"),
            "transaction_reference": h.get("transaction_reference"),
            "notes": h.get("notes"),
            "processed_by": h.get("processed_by"),
            "processed_at": h.get("processed_at")
        })

    return result

@api_router.post("/merry-go-round/advance")
async def advance_merry_go_round(data: MerryGoRoundAdvance, current_user: dict = Depends(get_current_user)):
    mgr = await db.merry_go_rounds.find_one({"_id": ObjectId(data.round_id)})
    if not mgr:
        raise HTTPException(status_code=404, detail="Merry-Go-Round not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can advance merry-go-rounds")

    # Check if already completed
    if mgr["status"] == "completed":
        raise HTTPException(status_code=400, detail="Merry-Go-Round is already completed")

    current_pos = mgr.get("current_position", 0)
    if current_pos >= len(mgr["member_order"]):
        raise HTTPException(status_code=400, detail="All members have already received payouts")

    # Get current member
    current_member_id = mgr["member_order"][current_pos]

    # If funding_source is "balance", check if chama has sufficient balance
    if mgr.get("funding_source") == "balance":
        # Calculate current balance
        contributions = await db.contributions.find({"chama_id": mgr["chama_id"]}).to_list(10000)
        total_contributions = sum(c["amount"] for c in contributions if c["status"] == "paid")

        loans = await db.loans.find({"chama_id": mgr["chama_id"], "status": "approved"}).to_list(10000)
        total_loans_issued = sum(l["amount"] for l in loans)

        repayments = await db.repayments.find({}).to_list(10000)
        loan_ids = [str(l["_id"]) for l in loans]
        total_repaid = sum(r["amount"] for r in repayments if r["loan_id"] in loan_ids)

        # Get total merry-go-round disbursements
        mgr_disbursements = await db.merry_go_round_disbursements.find({"chama_id": mgr["chama_id"]}).to_list(10000)
        total_mgr_disbursements = sum(d["amount"] for d in mgr_disbursements)

        current_balance = total_contributions - total_loans_issued + total_repaid - total_mgr_disbursements

        if current_balance < data.payout_amount:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient balance. Current balance: KES {current_balance:.2f}, Required: KES {data.payout_amount:.2f}"
            )

    # Add to history
    history_entry = {
        "member_id": current_member_id,
        "position": current_pos + 1,
        "amount": data.payout_amount,
        "payout_date": datetime.utcnow().isoformat().split('T')[0],
        "payment_method": data.payment_method,
        "transaction_reference": data.transaction_reference,
        "notes": data.notes,
        "processed_by": str(current_user["_id"]),
        "processed_at": datetime.utcnow().isoformat()
    }

    # Update position
    new_position = current_pos + 1
    new_status = "completed" if new_position >= len(mgr["member_order"]) else "active"

    await db.merry_go_rounds.update_one(
        {"_id": ObjectId(data.round_id)},
        {
            "$set": {
                "current_position": new_position,
                "status": new_status
            },
            "$push": {"history": history_entry}
        }
    )

    # If funding_source is "balance", record the disbursement
    if mgr.get("funding_source") == "balance":
        disbursement_dict = {
            "chama_id": mgr["chama_id"],
            "round_id": data.round_id,
            "round_name": mgr["name"],
            "member_id": current_member_id,
            "amount": data.payout_amount,
            "payment_method": data.payment_method,
            "transaction_reference": data.transaction_reference,
            "notes": data.notes,
            "position": current_pos + 1,
            "disbursed_by": str(current_user["_id"]),
            "disbursed_at": datetime.utcnow().isoformat()
        }
        await db.merry_go_round_disbursements.insert_one(disbursement_dict)

    # Audit log
    funding_info = f" (funded from {mgr.get('funding_source', 'contribution')})" if mgr.get("funding_source") else ""
    await db.audit_logs.insert_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "advance_merry_go_round",
        "details": f"Advanced {mgr['name']} to position {new_position}{funding_info}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "message": "Merry-Go-Round advanced successfully",
        "new_position": new_position,
        "status": new_status,
        "funding_source": mgr.get("funding_source", "contribution")
    }

@api_router.put("/merry-go-round/{round_id}")
async def update_merry_go_round_status(
    round_id: str,
    data: MerryGoRoundUpdate,
    current_user: dict = Depends(get_current_user)
):
    mgr = await db.merry_go_rounds.find_one({"_id": ObjectId(round_id)})
    if not mgr:
        raise HTTPException(status_code=404, detail="Merry-Go-Round not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update merry-go-rounds")

    await db.merry_go_rounds.update_one(
        {"_id": ObjectId(round_id)},
        {"$set": {"status": data.status}}
    )

    return {"message": "Merry-Go-Round status updated successfully"}

@api_router.delete("/merry-go-round/{round_id}")
async def delete_merry_go_round(round_id: str, current_user: dict = Depends(get_current_user)):
    mgr = await db.merry_go_rounds.find_one({"_id": ObjectId(round_id)})
    if not mgr:
        raise HTTPException(status_code=404, detail="Merry-Go-Round not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": mgr["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete merry-go-rounds")

    # Store round details for audit log before deletion
    round_name = mgr["name"]
    round_chama_id = mgr["chama_id"]
    total_members = len(mgr["member_order"])
    current_position = mgr.get("current_position", 0)
    funding_source = mgr.get("funding_source", "contribution")

    # Delete associated disbursement records if any
    if funding_source == "balance":
        disbursement_result = await db.merry_go_round_disbursements.delete_many({"round_id": round_id})
        logger.info(f"Deleted {disbursement_result.deleted_count} disbursement records for round {round_id}")

    # Delete the merry-go-round
    await db.merry_go_rounds.delete_one({"_id": ObjectId(round_id)})

    # Create audit log
    await db.audit_logs.insert_one({
        "chama_id": round_chama_id,
        "user_id": str(current_user["_id"]),
        "action": "delete_merry_go_round",
        "details": f"Deleted Merry-Go-Round: {round_name} (Position {current_position}/{total_members}, {funding_source} funded)",
        "timestamp": datetime.utcnow().isoformat(),
        "metadata": {
            "round_id": round_id,
            "round_name": round_name,
            "total_members": total_members,
            "current_position": current_position,
            "funding_source": funding_source
        }
    })

    logger.info(f"Admin {current_user['name']} deleted Merry-Go-Round {round_name} (ID: {round_id})")

    return {
        "message": "Merry-Go-Round deleted successfully",
        "details": {
            "round_name": round_name,
            "total_members": total_members,
            "current_position": current_position
        }
    }

# Investment Goals Endpoints
@api_router.post("/investment-goals/create")
async def create_investment_goal(data: InvestmentGoalCreate, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # For group goals, verify admin
    if data.goal_type == "group":
        if member["role"] != "admin":
            raise HTTPException(status_code=403, detail="Only admins can create group goals")

    goal_dict = {
        "chama_id": data.chama_id,
        "name": data.name,
        "description": data.description,
        "target_amount": data.target_amount,
        "current_amount": 0.0,
        "deadline": data.deadline,
        "goal_type": data.goal_type,  # group or personal
        "category": data.category,
        "icon": data.icon or "trophy",
        "color": data.color or "#4ECDC4",
        "status": "active",
        "created_by": str(current_user["_id"]),
        "member_id": str(member["_id"]) if data.goal_type == "personal" else None,
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.investment_goals.insert_one(goal_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "create_investment_goal",
        "details": f"Created {data.goal_type} goal: {data.name}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"goal_id": str(result.inserted_id), "message": "Investment goal created successfully"}

@api_router.get("/investment-goals/{chama_id}")
async def get_investment_goals(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get all group goals and user's personal goals
    goals = await db.investment_goals.find({
        "chama_id": chama_id,
        "$or": [
            {"goal_type": "group"},
            {"member_id": str(member["_id"])}
        ]
    }).to_list(1000)

    result = []
    for goal in goals:
        # Get contribution count
        contribution_count = await db.goal_contributions.count_documents({"goal_id": str(goal["_id"])})

        # Calculate progress percentage
        progress = (goal["current_amount"] / goal["target_amount"] * 100) if goal["target_amount"] > 0 else 0

        result.append({
            "id": str(goal["_id"]),
            "name": goal["name"],
            "description": goal.get("description"),
            "target_amount": goal["target_amount"],
            "current_amount": goal["current_amount"],
            "progress": round(progress, 2),
            "deadline": goal["deadline"],
            "goal_type": goal["goal_type"],
            "category": goal["category"],
            "icon": goal.get("icon", "trophy"),
            "color": goal.get("color", "#4ECDC4"),
            "status": goal["status"],
            "contribution_count": contribution_count,
            "created_at": goal["created_at"]
        })

    return result

@api_router.get("/investment-goals/detail/{goal_id}")
async def get_investment_goal_details(goal_id: str, current_user: dict = Depends(get_current_user)):
    goal = await db.investment_goals.find_one({"_id": ObjectId(goal_id)})
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": goal["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Verify access to personal goal
    if goal["goal_type"] == "personal" and goal["member_id"] != str(member["_id"]):
        raise HTTPException(status_code=403, detail="You can only view your own personal goals")

    # Get contributions
    contributions = await db.goal_contributions.find({"goal_id": goal_id}).sort("contribution_date", -1).to_list(100)

    contribution_list = []
    for contrib in contributions:
        # Get contributor details
        contrib_member = await db.members.find_one({"_id": ObjectId(contrib["member_id"])})
        contributor_name = "Unknown"
        if contrib_member:
            user = await db.users.find_one({"_id": ObjectId(contrib_member["user_id"])})
            contributor_name = user["name"] if user else "Unknown"

        contribution_list.append({
            "id": str(contrib["_id"]),
            "amount": contrib["amount"],
            "contributor_name": contributor_name,
            "contribution_date": contrib["contribution_date"],
            "notes": contrib.get("notes")
        })

    # Calculate progress
    progress = (goal["current_amount"] / goal["target_amount"] * 100) if goal["target_amount"] > 0 else 0

    return {
        "id": str(goal["_id"]),
        "name": goal["name"],
        "description": goal.get("description"),
        "target_amount": goal["target_amount"],
        "current_amount": goal["current_amount"],
        "progress": round(progress, 2),
        "deadline": goal["deadline"],
        "goal_type": goal["goal_type"],
        "category": goal["category"],
        "icon": goal.get("icon", "trophy"),
        "color": goal.get("color", "#4ECDC4"),
        "status": goal["status"],
        "contributions": contribution_list,
        "created_at": goal["created_at"]
    }

@api_router.put("/investment-goals/{goal_id}")
async def update_investment_goal(goal_id: str, data: InvestmentGoalUpdate, current_user: dict = Depends(get_current_user)):
    goal = await db.investment_goals.find_one({"_id": ObjectId(goal_id)})
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": goal["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Verify permission to update
    if goal["goal_type"] == "group":
        if member["role"] != "admin":
            raise HTTPException(status_code=403, detail="Only admins can update group goals")
    elif goal["member_id"] != str(member["_id"]):
        raise HTTPException(status_code=403, detail="You can only update your own personal goals")

    update_data = {}
    if data.name:
        update_data["name"] = data.name
    if data.description is not None:
        update_data["description"] = data.description
    if data.target_amount:
        update_data["target_amount"] = data.target_amount
    if data.deadline:
        update_data["deadline"] = data.deadline
    if data.status:
        update_data["status"] = data.status
    if data.category:
        update_data["category"] = data.category
    if data.icon:
        update_data["icon"] = data.icon
    if data.color:
        update_data["color"] = data.color

    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")

    await db.investment_goals.update_one(
        {"_id": ObjectId(goal_id)},
        {"$set": update_data}
    )

    return {"message": "Goal updated successfully"}

@api_router.delete("/investment-goals/{goal_id}")
async def delete_investment_goal(goal_id: str, current_user: dict = Depends(get_current_user)):
    goal = await db.investment_goals.find_one({"_id": ObjectId(goal_id)})
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": goal["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Verify permission to delete
    if goal["goal_type"] == "group":
        if member["role"] != "admin":
            raise HTTPException(status_code=403, detail="Only admins can delete group goals")
    elif goal["member_id"] != str(member["_id"]):
        raise HTTPException(status_code=403, detail="You can only delete your own personal goals")

    await db.investment_goals.delete_one({"_id": ObjectId(goal_id)})

    # Delete associated contributions
    await db.goal_contributions.delete_many({"goal_id": goal_id})

    return {"message": "Goal deleted successfully"}

# Goal Contributions Endpoints
@api_router.post("/goal-contributions/create")
async def create_goal_contribution(data: GoalContributionCreate, current_user: dict = Depends(get_current_user)):
    goal = await db.investment_goals.find_one({"_id": ObjectId(data.goal_id)})
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": goal["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Verify access for personal goals
    if goal["goal_type"] == "personal" and goal["member_id"] != str(member["_id"]):
        raise HTTPException(status_code=403, detail="You can only contribute to your own personal goals")

    contribution_date = data.contribution_date or datetime.utcnow().isoformat().split('T')[0]

    contrib_dict = {
        "goal_id": data.goal_id,
        "chama_id": goal["chama_id"],
        "member_id": str(member["_id"]),
        "amount": data.amount,
        "contribution_date": contribution_date,
        "notes": data.notes,
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.goal_contributions.insert_one(contrib_dict)

    # Update goal's current amount
    await db.investment_goals.update_one(
        {"_id": ObjectId(data.goal_id)},
        {"$inc": {"current_amount": data.amount}}
    )

    # Check if goal is completed
    updated_goal = await db.investment_goals.find_one({"_id": ObjectId(data.goal_id)})
    if updated_goal["current_amount"] >= updated_goal["target_amount"]:
        await db.investment_goals.update_one(
            {"_id": ObjectId(data.goal_id)},
            {"$set": {"status": "completed"}}
        )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": goal["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "contribute_to_goal",
        "details": f"Contributed KES {data.amount} to goal: {goal['name']}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"contribution_id": str(result.inserted_id), "message": "Contribution added successfully"}

@api_router.get("/goal-contributions/{goal_id}")
async def get_goal_contributions(goal_id: str, current_user: dict = Depends(get_current_user)):
    goal = await db.investment_goals.find_one({"_id": ObjectId(goal_id)})
    if not goal:
        raise HTTPException(status_code=404, detail="Goal not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": goal["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    contributions = await db.goal_contributions.find({"goal_id": goal_id}).sort("contribution_date", -1).to_list(100)

    result = []
    for contrib in contributions:
        contrib_member = await db.members.find_one({"_id": ObjectId(contrib["member_id"])})
        contributor_name = "Unknown"
        if contrib_member:
            user = await db.users.find_one({"_id": ObjectId(contrib_member["user_id"])})
            contributor_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(contrib["_id"]),
            "amount": contrib["amount"],
            "contributor_name": contributor_name,
            "contribution_date": contrib["contribution_date"],
            "notes": contrib.get("notes"),
            "created_at": contrib["created_at"]
        })

    return result

# Investments (Portfolio) Endpoints
@api_router.post("/investments/create")
async def create_investment(data: InvestmentCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can create investments")

    investment_dict = {
        "chama_id": data.chama_id,
        "name": data.name,
        "description": data.description,
        "investment_type": data.investment_type,
        "initial_amount": data.initial_amount,
        "current_value": data.current_value,
        "purchase_date": data.purchase_date,
        "maturity_date": data.maturity_date,
        "risk_level": data.risk_level,
        "notes": data.notes,
        "linked_goal_id": data.linked_goal_id,
        "status": "active",
        "total_returns": 0.0,
        "created_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.investments.insert_one(investment_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "create_investment",
        "details": f"Created investment: {data.name} - KES {data.initial_amount:,.2f}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"investment_id": str(result.inserted_id), "message": "Investment created successfully"}

@api_router.get("/investments/{chama_id}")
async def get_investments(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    investments = await db.investments.find({"chama_id": chama_id}).to_list(1000)

    result = []
    for inv in investments:
        # Calculate ROI
        roi = ((inv["current_value"] - inv["initial_amount"]) / inv["initial_amount"] * 100) if inv["initial_amount"] > 0 else 0

        # Get returns count
        returns_count = await db.investment_returns.count_documents({"investment_id": str(inv["_id"])})

        result.append({
            "id": str(inv["_id"]),
            "name": inv["name"],
            "description": inv.get("description"),
            "investment_type": inv["investment_type"],
            "initial_amount": inv["initial_amount"],
            "current_value": inv["current_value"],
            "purchase_date": inv["purchase_date"],
            "maturity_date": inv.get("maturity_date"),
            "risk_level": inv["risk_level"],
            "status": inv["status"],
            "total_returns": inv.get("total_returns", 0.0),
            "roi": round(roi, 2),
            "returns_count": returns_count,
            "linked_goal_id": inv.get("linked_goal_id"),
            "created_at": inv["created_at"]
        })

    return result

@api_router.get("/investments/detail/{investment_id}")
async def get_investment_details(investment_id: str, current_user: dict = Depends(get_current_user)):
    inv = await db.investments.find_one({"_id": ObjectId(investment_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Investment not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": inv["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get returns
    returns = await db.investment_returns.find({"investment_id": investment_id}).sort("return_date", -1).to_list(100)

    returns_list = []
    for ret in returns:
        returns_list.append({
            "id": str(ret["_id"]),
            "amount": ret["return_amount"],
            "return_date": ret["return_date"],
            "return_type": ret["return_type"],
            "notes": ret.get("notes"),
            "created_at": ret["created_at"]
        })

    # Calculate ROI
    roi = ((inv["current_value"] - inv["initial_amount"]) / inv["initial_amount"] * 100) if inv["initial_amount"] > 0 else 0

    return {
        "id": str(inv["_id"]),
        "name": inv["name"],
        "description": inv.get("description"),
        "investment_type": inv["investment_type"],
        "initial_amount": inv["initial_amount"],
        "current_value": inv["current_value"],
        "purchase_date": inv["purchase_date"],
        "maturity_date": inv.get("maturity_date"),
        "risk_level": inv["risk_level"],
        "notes": inv.get("notes"),
        "linked_goal_id": inv.get("linked_goal_id"),
        "status": inv["status"],
        "total_returns": inv.get("total_returns", 0.0),
        "roi": round(roi, 2),
        "returns": returns_list,
        "created_at": inv["created_at"]
    }

@api_router.put("/investments/{investment_id}")
async def update_investment(investment_id: str, data: InvestmentUpdate, current_user: dict = Depends(get_current_user)):
    inv = await db.investments.find_one({"_id": ObjectId(investment_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Investment not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": inv["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update investments")

    update_data = {}
    if data.name:
        update_data["name"] = data.name
    if data.description is not None:
        update_data["description"] = data.description
    if data.current_value is not None:
        update_data["current_value"] = data.current_value
    if data.maturity_date:
        update_data["maturity_date"] = data.maturity_date
    if data.status:
        update_data["status"] = data.status
    if data.notes is not None:
        update_data["notes"] = data.notes

    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")

    await db.investments.update_one(
        {"_id": ObjectId(investment_id)},
        {"$set": update_data}
    )

    return {"message": "Investment updated successfully"}

@api_router.delete("/investments/{investment_id}")
async def delete_investment(investment_id: str, current_user: dict = Depends(get_current_user)):
    inv = await db.investments.find_one({"_id": ObjectId(investment_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Investment not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": inv["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete investments")

    await db.investments.delete_one({"_id": ObjectId(investment_id)})

    # Delete associated returns
    await db.investment_returns.delete_many({"investment_id": investment_id})

    return {"message": "Investment deleted successfully"}

# Investment Returns Endpoints
@api_router.post("/investment-returns/create")
async def create_investment_return(data: InvestmentReturnCreate, current_user: dict = Depends(get_current_user)):
    inv = await db.investments.find_one({"_id": ObjectId(data.investment_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Investment not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": inv["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can record investment returns")

    return_dict = {
        "investment_id": data.investment_id,
        "chama_id": inv["chama_id"],
        "return_amount": data.return_amount,
        "return_date": data.return_date,
        "return_type": data.return_type,
        "notes": data.notes,
        "recorded_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.investment_returns.insert_one(return_dict)

    # Update investment's total returns
    await db.investments.update_one(
        {"_id": ObjectId(data.investment_id)},
        {"$inc": {"total_returns": data.return_amount}}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": inv["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "record_investment_return",
        "details": f"Recorded {data.return_type} return: KES {data.return_amount:,.2f} for {inv['name']}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"return_id": str(result.inserted_id), "message": "Investment return recorded successfully"}

@api_router.get("/investment-returns/{investment_id}")
async def get_investment_returns(investment_id: str, current_user: dict = Depends(get_current_user)):
    inv = await db.investments.find_one({"_id": ObjectId(investment_id)})
    if not inv:
        raise HTTPException(status_code=404, detail="Investment not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": inv["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    returns = await db.investment_returns.find({"investment_id": investment_id}).sort("return_date", -1).to_list(100)

    result = []
    for ret in returns:
        result.append({
            "id": str(ret["_id"]),
            "amount": ret["return_amount"],
            "return_date": ret["return_date"],
            "return_type": ret["return_type"],
            "notes": ret.get("notes"),
            "created_at": ret["created_at"]
        })

    return result

# Investment Analytics
@api_router.get("/investments/analytics/{chama_id}")
async def get_investment_analytics(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get all investments
    investments = await db.investments.find({"chama_id": chama_id}).to_list(1000)

    total_invested = sum(inv["initial_amount"] for inv in investments)
    total_current_value = sum(inv["current_value"] for inv in investments)
    total_returns = sum(inv.get("total_returns", 0.0) for inv in investments)

    overall_roi = ((total_current_value - total_invested) / total_invested * 100) if total_invested > 0 else 0

    # Group by investment type
    by_type = {}
    for inv in investments:
        inv_type = inv["investment_type"]
        if inv_type not in by_type:
            by_type[inv_type] = {
                "count": 0,
                "total_invested": 0,
                "total_current_value": 0,
                "total_returns": 0
            }
        by_type[inv_type]["count"] += 1
        by_type[inv_type]["total_invested"] += inv["initial_amount"]
        by_type[inv_type]["total_current_value"] += inv["current_value"]
        by_type[inv_type]["total_returns"] += inv.get("total_returns", 0.0)

    # Get all goals
    goals = await db.investment_goals.find({"chama_id": chama_id}).to_list(1000)

    total_goal_target = sum(g["target_amount"] for g in goals if g["status"] == "active")
    total_goal_current = sum(g["current_amount"] for g in goals if g["status"] == "active")
    completed_goals = len([g for g in goals if g["status"] == "completed"])

    return {
        "total_invested": total_invested,
        "total_current_value": total_current_value,
        "total_returns": total_returns,
        "overall_roi": round(overall_roi, 2),
        "total_profit_loss": total_current_value - total_invested + total_returns,
        "investment_count": len(investments),
        "by_type": by_type,
        "goals_summary": {
            "active_goals": len([g for g in goals if g["status"] == "active"]),
            "completed_goals": completed_goals,
            "total_target": total_goal_target,
            "total_saved": total_goal_current,
            "overall_progress": (total_goal_current / total_goal_target * 100) if total_goal_target > 0 else 0
        }
    }

# Meetings Endpoints
@api_router.post("/meetings/create")
async def create_meeting(data: MeetingCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin or secretary
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] not in ["admin", "secretary"]:
        raise HTTPException(status_code=403, detail="Only admins and secretaries can create meetings")

    meeting_dict = {
        "chama_id": data.chama_id,
        "title": data.title,
        "description": data.description,
        "agenda": data.agenda,
        "start_time": data.start_time,
        "end_time": data.end_time,
        "location": data.location,
        "virtual_link": data.virtual_link,
        "recurring": data.recurring,
        "recurring_frequency": data.recurring_frequency,
        "status": "scheduled",
        "created_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat(),
        "rsvp_count": {
            "attending": 0,
            "not_attending": 0,
            "maybe": 0
        },
        "attendance_count": 0
    }

    result = await db.meetings.insert_one(meeting_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "create_meeting",
        "details": f"Created meeting: {data.title}",
        "timestamp": datetime.utcnow().isoformat()
    })

    # Create announcement for the meeting
    await db.announcements.insert_one({
        "chama_id": data.chama_id,
        "title": f"Meeting Scheduled: {data.title}",
        "message": f"A new meeting has been scheduled for {data.start_time}. Location: {data.location or 'Virtual'}",
        "created_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    })

    return {"meeting_id": str(result.inserted_id), "message": "Meeting created successfully"}

@api_router.get("/meetings/{chama_id}")
async def get_meetings(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    meetings = await db.meetings.find({"chama_id": chama_id}).sort("start_time", -1).to_list(100)

    result = []
    for meeting in meetings:
        # Get creator details
        creator = await db.users.find_one({"_id": ObjectId(meeting["created_by"])})
        creator_name = creator["name"] if creator else "Unknown"

        # Check user's RSVP status
        rsvp = await db.meeting_rsvps.find_one({
            "meeting_id": str(meeting["_id"]),
            "member_id": str(member["_id"])
        })
        user_rsvp = rsvp["status"] if rsvp else None

        # Check if user attended
        attendance = await db.meeting_attendance.find_one({
            "meeting_id": str(meeting["_id"]),
            "member_id": str(member["_id"])
        })
        user_attended = bool(attendance)

        result.append({
            "id": str(meeting["_id"]),
            "title": meeting["title"],
            "description": meeting.get("description"),
            "agenda": meeting.get("agenda"),
            "start_time": meeting["start_time"],
            "end_time": meeting.get("end_time"),
            "location": meeting.get("location"),
            "virtual_link": meeting.get("virtual_link"),
            "status": meeting["status"],
            "created_by": creator_name,
            "rsvp_count": meeting.get("rsvp_count", {"attending": 0, "not_attending": 0, "maybe": 0}),
            "attendance_count": meeting.get("attendance_count", 0),
            "user_rsvp": user_rsvp,
            "user_attended": user_attended,
            "created_at": meeting["created_at"]
        })

    return result

@api_router.get("/meetings/detail/{meeting_id}")
async def get_meeting_details(meeting_id: str, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get RSVPs
    rsvps = await db.meeting_rsvps.find({"meeting_id": meeting_id}).to_list(1000)
    rsvp_list = []
    for rsvp in rsvps:
        rsvp_member = await db.members.find_one({"_id": ObjectId(rsvp["member_id"])})
        member_name = "Unknown"
        if rsvp_member:
            user = await db.users.find_one({"_id": ObjectId(rsvp_member["user_id"])})
            member_name = user["name"] if user else "Unknown"

        rsvp_list.append({
            "member_name": member_name,
            "status": rsvp["status"],
            "rsvp_at": rsvp["rsvp_at"]
        })

    # Get attendance
    attendance_records = await db.meeting_attendance.find({"meeting_id": meeting_id}).to_list(1000)
    attendance_list = []
    for att in attendance_records:
        att_member = await db.members.find_one({"_id": ObjectId(att["member_id"])})
        member_name = "Unknown"
        if att_member:
            user = await db.users.find_one({"_id": ObjectId(att_member["user_id"])})
            member_name = user["name"] if user else "Unknown"

        attendance_list.append({
            "member_name": member_name,
            "checked_in_at": att["checked_in_at"],
            "method": att.get("method", "manual")
        })

    # Get minutes if exists
    minutes = await db.meeting_minutes.find_one({"meeting_id": meeting_id})
    minutes_data = None
    if minutes:
        minutes_author = await db.users.find_one({"_id": ObjectId(minutes["created_by"])})
        minutes_data = {
            "id": str(minutes["_id"]),
            "content": minutes["content"],
            "decisions": minutes.get("decisions"),
            "action_items": minutes.get("action_items"),
            "created_by": minutes_author["name"] if minutes_author else "Unknown",
            "created_at": minutes["created_at"]
        }

    # Check user's RSVP
    user_rsvp = await db.meeting_rsvps.find_one({
        "meeting_id": meeting_id,
        "member_id": str(member["_id"])
    })

    # Check if user attended
    user_attendance = await db.meeting_attendance.find_one({
        "meeting_id": meeting_id,
        "member_id": str(member["_id"])
    })

    return {
        "id": str(meeting["_id"]),
        "title": meeting["title"],
        "description": meeting.get("description"),
        "agenda": meeting.get("agenda"),
        "start_time": meeting["start_time"],
        "end_time": meeting.get("end_time"),
        "location": meeting.get("location"),
        "virtual_link": meeting.get("virtual_link"),
        "status": meeting["status"],
        "recurring": meeting.get("recurring", False),
        "recurring_frequency": meeting.get("recurring_frequency"),
        "rsvp_count": meeting.get("rsvp_count", {"attending": 0, "not_attending": 0, "maybe": 0}),
        "attendance_count": meeting.get("attendance_count", 0),
        "rsvps": rsvp_list,
        "attendance": attendance_list,
        "minutes": minutes_data,
        "user_rsvp": user_rsvp["status"] if user_rsvp else None,
        "user_attended": bool(user_attendance),
        "created_at": meeting["created_at"]
    }

@api_router.put("/meetings/{meeting_id}")
async def update_meeting(meeting_id: str, data: MeetingUpdate, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify admin or secretary
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] not in ["admin", "secretary"]:
        raise HTTPException(status_code=403, detail="Only admins and secretaries can update meetings")

    update_data = {}
    if data.title:
        update_data["title"] = data.title
    if data.description is not None:
        update_data["description"] = data.description
    if data.agenda is not None:
        update_data["agenda"] = data.agenda
    if data.start_time:
        update_data["start_time"] = data.start_time
    if data.end_time is not None:
        update_data["end_time"] = data.end_time
    if data.location is not None:
        update_data["location"] = data.location
    if data.virtual_link is not None:
        update_data["virtual_link"] = data.virtual_link
    if data.status:
        update_data["status"] = data.status

    if not update_data:
        raise HTTPException(status_code=400, detail="No data to update")

    await db.meetings.update_one(
        {"_id": ObjectId(meeting_id)},
        {"$set": update_data}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "update_meeting",
        "details": f"Updated meeting: {meeting['title']}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"message": "Meeting updated successfully"}

@api_router.delete("/meetings/{meeting_id}")
async def delete_meeting(meeting_id: str, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can delete meetings")

    await db.meetings.delete_one({"_id": ObjectId(meeting_id)})

    # Delete related data
    await db.meeting_rsvps.delete_many({"meeting_id": meeting_id})
    await db.meeting_attendance.delete_many({"meeting_id": meeting_id})
    await db.meeting_minutes.delete_many({"meeting_id": meeting_id})

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "delete_meeting",
        "details": f"Deleted meeting: {meeting['title']}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"message": "Meeting deleted successfully"}

@api_router.post("/meetings/{meeting_id}/rsvp")
async def rsvp_meeting(meeting_id: str, data: RSVPCreate, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Check if RSVP already exists
    existing_rsvp = await db.meeting_rsvps.find_one({
        "meeting_id": meeting_id,
        "member_id": str(member["_id"])
    })

    if existing_rsvp:
        # Update existing RSVP - adjust counts
        old_status = existing_rsvp["status"]
        await db.meeting_rsvps.update_one(
            {"_id": existing_rsvp["_id"]},
            {"$set": {
                "status": data.status,
                "rsvp_at": datetime.utcnow().isoformat()
            }}
        )

        # Update meeting RSVP counts
        await db.meetings.update_one(
            {"_id": ObjectId(meeting_id)},
            {
                "$inc": {
                    f"rsvp_count.{old_status}": -1,
                    f"rsvp_count.{data.status}": 1
                }
            }
        )
    else:
        # Create new RSVP
        rsvp_dict = {
            "meeting_id": meeting_id,
            "chama_id": meeting["chama_id"],
            "member_id": str(member["_id"]),
            "user_id": str(current_user["_id"]),
            "status": data.status,
            "rsvp_at": datetime.utcnow().isoformat()
        }
        await db.meeting_rsvps.insert_one(rsvp_dict)

        # Update meeting RSVP count
        await db.meetings.update_one(
            {"_id": ObjectId(meeting_id)},
            {"$inc": {f"rsvp_count.{data.status}": 1}}
        )

    return {"message": "RSVP recorded successfully", "status": data.status}

@api_router.post("/meetings/{meeting_id}/attendance")
async def mark_attendance(meeting_id: str, data: AttendanceCreate, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Check if already marked attendance
    existing_attendance = await db.meeting_attendance.find_one({
        "meeting_id": meeting_id,
        "member_id": str(member["_id"])
    })

    if existing_attendance:
        return {"message": "Attendance already recorded", "checked_in_at": existing_attendance["checked_in_at"]}

    # Create attendance record
    attendance_dict = {
        "meeting_id": meeting_id,
        "chama_id": meeting["chama_id"],
        "member_id": str(member["_id"]),
        "user_id": str(current_user["_id"]),
        "checked_in_at": datetime.utcnow().isoformat(),
        "method": data.method,
        "notes": data.notes
    }
    await db.meeting_attendance.insert_one(attendance_dict)

    # Update meeting attendance count
    await db.meetings.update_one(
        {"_id": ObjectId(meeting_id)},
        {"$inc": {"attendance_count": 1}}
    )

    return {"message": "Attendance recorded successfully"}

@api_router.post("/meetings/{meeting_id}/minutes")
async def save_meeting_minutes(meeting_id: str, data: MinutesCreate, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify admin or secretary
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] not in ["admin", "secretary"]:
        raise HTTPException(status_code=403, detail="Only admins and secretaries can save minutes")

    # Check if minutes already exist
    existing_minutes = await db.meeting_minutes.find_one({"meeting_id": meeting_id})

    if existing_minutes:
        # Update existing minutes
        await db.meeting_minutes.update_one(
            {"_id": existing_minutes["_id"]},
            {"$set": {
                "content": data.content,
                "decisions": data.decisions,
                "action_items": data.action_items,
                "updated_by": str(current_user["_id"]),
                "updated_at": datetime.utcnow().isoformat()
            }}
        )
        minutes_id = str(existing_minutes["_id"])
    else:
        # Create new minutes
        minutes_dict = {
            "meeting_id": meeting_id,
            "chama_id": meeting["chama_id"],
            "content": data.content,
            "decisions": data.decisions,
            "action_items": data.action_items,
            "created_by": str(current_user["_id"]),
            "created_at": datetime.utcnow().isoformat()
        }
        result = await db.meeting_minutes.insert_one(minutes_dict)
        minutes_id = str(result.inserted_id)

    # Update meeting status to completed if not already
    if meeting["status"] != "completed":
        await db.meetings.update_one(
            {"_id": ObjectId(meeting_id)},
            {"$set": {"status": "completed"}}
        )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "save_meeting_minutes",
        "details": f"Saved minutes for meeting: {meeting['title']}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"minutes_id": minutes_id, "message": "Minutes saved successfully"}

@api_router.get("/meetings/{meeting_id}/minutes")
async def get_meeting_minutes(meeting_id: str, current_user: dict = Depends(get_current_user)):
    meeting = await db.meetings.find_one({"_id": ObjectId(meeting_id)})
    if not meeting:
        raise HTTPException(status_code=404, detail="Meeting not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": meeting["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    minutes = await db.meeting_minutes.find_one({"meeting_id": meeting_id})
    if not minutes:
        raise HTTPException(status_code=404, detail="No minutes found for this meeting")

    # Get author details
    author = await db.users.find_one({"_id": ObjectId(minutes["created_by"])})
    author_name = author["name"] if author else "Unknown"

    return {
        "id": str(minutes["_id"]),
        "meeting_id": meeting_id,
        "content": minutes["content"],
        "decisions": minutes.get("decisions"),
        "action_items": minutes.get("action_items"),
        "created_by": author_name,
        "created_at": minutes["created_at"],
        "updated_at": minutes.get("updated_at")
    }

# Welfare Fund Endpoints
@api_router.get("/welfare/settings/{chama_id}")
async def get_welfare_settings(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    # Get or create welfare settings
    settings = chama.get("welfare_settings", {
        "enabled": True,
        "monthly_contribution": 1000,
        "max_request_amount": 50000,
        "approval_threshold": 0.75,
        "processing_days": 5
    })

    return settings

@api_router.put("/welfare/settings/{chama_id}")
async def update_welfare_settings(
    chama_id: str,
    settings: WelfareSettingsUpdate,
    current_user: dict = Depends(get_current_user)
):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update welfare settings")

    # Update settings
    await db.chamas.update_one(
        {"_id": ObjectId(chama_id)},
        {"$set": {"welfare_settings": settings.dict()}}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "action": "update_welfare_settings",
        "details": "Updated welfare fund settings",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"message": "Welfare settings updated successfully", "settings": settings.dict()}

@api_router.get("/welfare/balance/{chama_id}")
async def get_welfare_balance(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Calculate total contributions
    contributions = await db.welfare_contributions.find({"chama_id": chama_id}).to_list(10000)
    total_contributions = sum(c["amount"] for c in contributions)

    # Calculate total disbursements
    disbursements = await db.welfare_requests.find({
        "chama_id": chama_id,
        "status": "disbursed"
    }).to_list(10000)
    total_disbursed = sum(d["amount"] for d in disbursements)

    # Current balance
    current_balance = total_contributions - total_disbursed

    # Count members helped
    unique_members_helped = len(set(d["member_id"] for d in disbursements))

    return {
        "current_balance": current_balance,
        "total_contributions": total_contributions,
        "total_disbursed": total_disbursed,
        "members_helped": unique_members_helped,
        "total_requests": len(disbursements)
    }

@api_router.post("/welfare/contribute/{chama_id}")
async def make_welfare_contribution(
    chama_id: str,
    data: WelfareContributionCreate,
    current_user: dict = Depends(get_current_user)
):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Create contribution
    contribution_dict = {
        "chama_id": chama_id,
        "member_id": str(member["_id"]),
        "user_id": str(current_user["_id"]),
        "amount": data.amount,
        "contribution_date": data.contribution_date or datetime.utcnow().isoformat().split('T')[0],
        "notes": data.notes,
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.welfare_contributions.insert_one(contribution_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "action": "welfare_contribution",
        "details": f"Contributed KES {data.amount} to welfare fund",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "contribution_id": str(result.inserted_id),
        "message": "Welfare contribution recorded successfully"
    }

@api_router.get("/welfare/contributions/{chama_id}")
async def get_welfare_contributions(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    contributions = await db.welfare_contributions.find({"chama_id": chama_id}).sort("created_at", -1).to_list(1000)

    result = []
    for c in contributions:
        member_doc = await db.members.find_one({"_id": ObjectId(c["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(c["_id"]),
            "member_name": member_name,
            "amount": c["amount"],
            "contribution_date": c["contribution_date"],
            "notes": c.get("notes"),
            "created_at": c["created_at"]
        })

    return result

@api_router.post("/welfare/request")
async def create_welfare_request(data: WelfareRequestCreate, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get welfare settings
    chama = await db.chamas.find_one({"_id": ObjectId(data.chama_id)})
    welfare_settings = chama.get("welfare_settings", {})
    max_amount = welfare_settings.get("max_request_amount", 50000)

    # Validate amount
    if data.amount > max_amount:
        raise HTTPException(
            status_code=400,
            detail=f"Request amount exceeds maximum allowed (KES {max_amount})"
        )

    # Create request
    request_dict = {
        "chama_id": data.chama_id,
        "member_id": str(member["_id"]),
        "user_id": str(current_user["_id"]),
        "request_type": data.request_type,
        "amount": data.amount,
        "reason": data.reason,
        "description": data.description,
        "supporting_documents": data.supporting_documents or [],
        "status": "pending",
        "votes_for": 0,
        "votes_against": 0,
        "voters": [],
        "created_at": datetime.utcnow().isoformat(),
        "updated_at": datetime.utcnow().isoformat()
    }

    result = await db.welfare_requests.insert_one(request_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "welfare_request_created",
        "details": f"Created {data.request_type} welfare request for KES {data.amount}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "request_id": str(result.inserted_id),
        "message": "Welfare request submitted successfully"
    }

@api_router.get("/welfare/requests/{chama_id}")
async def get_welfare_requests(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get total active members count for participation calculation
    total_members = await db.members.count_documents({
        "chama_id": chama_id,
        "status": "active"
    })

    requests = await db.welfare_requests.find({"chama_id": chama_id}).sort("created_at", -1).to_list(1000)

    result = []
    for r in requests:
        member_doc = await db.members.find_one({"_id": ObjectId(r["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(r["_id"]),
            "member_id": r["member_id"],
            "member_name": member_name,
            "user_id": r["user_id"],
            "request_type": r["request_type"],
            "amount": r["amount"],
            "reason": r["reason"],
            "description": r.get("description"),
            "status": r["status"],
            "votes": {
                "for": r.get("votes_for", 0),
                "against": r.get("votes_against", 0)
            },
            "voters": r.get("voters", []),
            "total_members": total_members,
            "created_at": r["created_at"],
            "disbursement_date": r.get("disbursement_date"),
            "supporting_documents": r.get("supporting_documents", [])
        })

    return result

@api_router.get("/welfare/request/{request_id}")
async def get_welfare_request_details(request_id: str, current_user: dict = Depends(get_current_user)):
    request = await db.welfare_requests.find_one({"_id": ObjectId(request_id)})
    if not request:
        raise HTTPException(status_code=404, detail="Welfare request not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get member details
    member_doc = await db.members.find_one({"_id": ObjectId(request["member_id"])})
    member_name = "Unknown"
    if member_doc:
        user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
        member_name = user["name"] if user else "Unknown"

    return {
        "id": str(request["_id"]),
        "member_id": request["member_id"],
        "member_name": member_name,
        "request_type": request["request_type"],
        "amount": request["amount"],
        "reason": request["reason"],
        "description": request.get("description"),
        "status": request["status"],
        "votes": {
            "for": request.get("votes_for", 0),
            "against": request.get("votes_against", 0)
        },
        "voters": request.get("voters", []),
        "created_at": request["created_at"],
        "updated_at": request.get("updated_at"),
        "admin_notes": request.get("admin_notes"),
        "disbursement_date": request.get("disbursement_date"),
        "disbursement_method": request.get("disbursement_method"),
        "transaction_reference": request.get("transaction_reference"),
        "supporting_documents": request.get("supporting_documents", [])
    }

@api_router.post("/welfare/vote")
async def vote_on_welfare_request(data: WelfareVoteCreate, current_user: dict = Depends(get_current_user)):
    request = await db.welfare_requests.find_one({"_id": ObjectId(data.request_id)})
    if not request:
        raise HTTPException(status_code=404, detail="Welfare request not found")

    # Verify membership
    member = await db.members.find_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Prevent voting on own request
    if request["user_id"] == str(current_user["_id"]):
        raise HTTPException(status_code=403, detail="You cannot vote on your own welfare request")

    # Check if already voted
    voters = request.get("voters", [])
    user_id_str = str(current_user["_id"])

    # Find if user has already voted
    existing_vote = None
    for i, voter in enumerate(voters):
        if voter["user_id"] == user_id_str:
            existing_vote = i
            break

    # Remove previous vote if exists
    if existing_vote is not None:
        old_vote = voters[existing_vote]["vote"]
        if old_vote == "approve":
            request["votes_for"] = request.get("votes_for", 1) - 1
        else:
            request["votes_against"] = request.get("votes_against", 1) - 1
        voters.pop(existing_vote)

    # Add new vote
    voters.append({
        "user_id": user_id_str,
        "vote": data.vote,
        "comment": data.comment,
        "voted_at": datetime.utcnow().isoformat()
    })

    # Update vote counts
    if data.vote == "approve":
        votes_for = request.get("votes_for", 0) + 1
        votes_against = request.get("votes_against", 0)
    else:
        votes_for = request.get("votes_for", 0)
        votes_against = request.get("votes_against", 0) + 1

    # Update request
    await db.welfare_requests.update_one(
        {"_id": ObjectId(data.request_id)},
        {"$set": {
            "votes_for": votes_for,
            "votes_against": votes_against,
            "voters": voters,
            "updated_at": datetime.utcnow().isoformat()
        }}
    )

    # Check if approval threshold is met
    chama = await db.chamas.find_one({"_id": ObjectId(request["chama_id"])})
    welfare_settings = chama.get("welfare_settings", {})
    approval_threshold = welfare_settings.get("approval_threshold", 0.75)

    # Get total active members
    total_members = await db.members.count_documents({
        "chama_id": request["chama_id"],
        "status": "active"
    })

    # Calculate approval percentage
    total_votes = votes_for + votes_against
    if total_votes >= total_members * 0.5:  # At least 50% participation
        approval_rate = votes_for / total_votes if total_votes > 0 else 0

        if approval_rate >= approval_threshold:
            # Auto-approve
            await db.welfare_requests.update_one(
                {"_id": ObjectId(data.request_id)},
                {"$set": {
                    "status": "approved",
                    "approved_at": datetime.utcnow().isoformat(),
                    "updated_at": datetime.utcnow().isoformat()
                }}
            )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "welfare_vote",
        "details": f"Voted {data.vote} on welfare request",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "message": "Vote recorded successfully",
        "votes": {
            "for": votes_for,
            "against": votes_against
        }
    }

@api_router.put("/welfare/request/{request_id}")
async def update_welfare_request(
    request_id: str,
    data: WelfareRequestUpdate,
    current_user: dict = Depends(get_current_user)
):
    request = await db.welfare_requests.find_one({"_id": ObjectId(request_id)})
    if not request:
        raise HTTPException(status_code=404, detail="Welfare request not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update welfare requests")

    update_data = {"updated_at": datetime.utcnow().isoformat()}

    if data.status:
        update_data["status"] = data.status
        if data.status == "approved":
            update_data["approved_at"] = datetime.utcnow().isoformat()
        elif data.status == "disbursed":
            update_data["disbursement_date"] = data.disbursement_date or datetime.utcnow().isoformat().split('T')[0]
            if data.disbursement_method:
                update_data["disbursement_method"] = data.disbursement_method
            if data.transaction_reference:
                update_data["transaction_reference"] = data.transaction_reference

    if data.admin_notes:
        update_data["admin_notes"] = data.admin_notes

    await db.welfare_requests.update_one(
        {"_id": ObjectId(request_id)},
        {"$set": update_data}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "welfare_request_updated",
        "details": f"Updated welfare request status to {data.status}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"message": "Welfare request updated successfully"}

@api_router.get("/welfare/analytics/{chama_id}")
async def get_welfare_analytics(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get all requests
    requests = await db.welfare_requests.find({"chama_id": chama_id}).to_list(10000)

    # Analytics by type
    by_type = {}
    for r in requests:
        req_type = r["request_type"]
        if req_type not in by_type:
            by_type[req_type] = {"count": 0, "total_amount": 0}
        by_type[req_type]["count"] += 1
        if r["status"] == "disbursed":
            by_type[req_type]["total_amount"] += r["amount"]

    # Analytics by status
    by_status = {}
    for r in requests:
        status = r["status"]
        if status not in by_status:
            by_status[status] = 0
        by_status[status] += 1

    # Get balance info
    contributions = await db.welfare_contributions.find({"chama_id": chama_id}).to_list(10000)
    total_contributions = sum(c["amount"] for c in contributions)

    disbursements = [r for r in requests if r["status"] == "disbursed"]
    total_disbursed = sum(d["amount"] for d in disbursements)

    return {
        "by_type": by_type,
        "by_status": by_status,
        "total_requests": len(requests),
        "total_contributions": total_contributions,
        "total_disbursed": total_disbursed,
        "current_balance": total_contributions - total_disbursed,
        "members_helped": len(set(d["member_id"] for d in disbursements))
    }

@api_router.get("/welfare/disbursements/{chama_id}")
async def get_welfare_disbursements(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    disbursements = await db.welfare_requests.find({
        "chama_id": chama_id,
        "status": "disbursed"
    }).sort("disbursement_date", -1).to_list(1000)

    result = []
    for d in disbursements:
        member_doc = await db.members.find_one({"_id": ObjectId(d["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(d["_id"]),
            "member_name": member_name,
            "request_type": d["request_type"],
            "amount": d["amount"],
            "reason": d["reason"],
            "disbursement_date": d.get("disbursement_date"),
            "disbursement_method": d.get("disbursement_method"),
            "transaction_reference": d.get("transaction_reference")
        })

    return result

@api_router.post("/welfare/disburse")
async def disburse_welfare_funds(data: WelfareDisbursementCreate, current_user: dict = Depends(get_current_user)):
    # Get the welfare request
    request = await db.welfare_requests.find_one({"_id": ObjectId(data.request_id)})
    if not request:
        raise HTTPException(status_code=404, detail="Welfare request not found")

    # Verify admin
    member = await db.members.find_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can disburse welfare funds")

    # Check if request is approved
    if request["status"] != "approved":
        raise HTTPException(status_code=400, detail="Only approved requests can be disbursed")

    # Validate disbursement source
    if data.disbursement_source not in ["sacco_account", "cash"]:
        raise HTTPException(status_code=400, detail="Invalid disbursement source. Must be 'sacco_account' or 'cash'")

    # If disbursing from SACCO account, check balance
    if data.disbursement_source == "sacco_account":
        # Get current balance
        contributions = await db.welfare_contributions.find({"chama_id": request["chama_id"]}).to_list(10000)
        total_contributions = sum(c["amount"] for c in contributions)

        disbursements = await db.welfare_requests.find({
            "chama_id": request["chama_id"],
            "status": "disbursed"
        }).to_list(10000)
        total_disbursed = sum(d["amount"] for d in disbursements)

        current_balance = total_contributions - total_disbursed

        if current_balance < request["amount"]:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient welfare fund balance. Current balance: KES {current_balance}, Required: KES {request['amount']}"
            )

    # Update request status to disbursed
    await db.welfare_requests.update_one(
        {"_id": ObjectId(data.request_id)},
        {"$set": {
            "status": "disbursed",
            "disbursement_date": datetime.utcnow().isoformat().split('T')[0],
            "disbursement_method": data.disbursement_method,
            "disbursement_source": data.disbursement_source,
            "transaction_reference": data.transaction_reference,
            "disbursed_by": str(current_user["_id"]),
            "disbursement_notes": data.notes,
            "updated_at": datetime.utcnow().isoformat()
        }}
    )

    # Get member details for logging
    member_doc = await db.members.find_one({"_id": ObjectId(request["member_id"])})
    member_name = "Unknown"
    if member_doc:
        user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
        member_name = user["name"] if user else "Unknown"

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": request["chama_id"],
        "user_id": str(current_user["_id"]),
        "action": "welfare_disbursement",
        "details": f"Disbursed KES {request['amount']} to {member_name} from {data.disbursement_source} via {data.disbursement_method}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "message": "Welfare funds disbursed successfully",
        "request_id": data.request_id,
        "amount": request["amount"],
        "disbursement_source": data.disbursement_source,
        "disbursement_method": data.disbursement_method
    }

# Share Management Endpoints
@api_router.get("/shares/settings/{chama_id}")
async def get_share_settings(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    share_settings = chama.get("share_settings", {})

    return {
        "enabled": share_settings.get("enabled", True),
        "total_shares": share_settings.get("total_shares", 1000),
        "share_price": share_settings.get("share_price", 5000.0),
        "min_shares_per_member": share_settings.get("min_shares_per_member", 1),
        "max_shares_per_member": share_settings.get("max_shares_per_member"),
        "allow_share_transfer": share_settings.get("allow_share_transfer", True),
        "total_shares_allocated": share_settings.get("total_shares_allocated", 0),
        "total_share_capital": share_settings.get("total_share_capital", 0.0),
        "nav_per_share": share_settings.get("nav_per_share", share_settings.get("share_price", 5000.0))
    }

@api_router.put("/shares/settings/{chama_id}")
async def update_share_settings(
    chama_id: str,
    settings: ShareSettingsUpdate,
    current_user: dict = Depends(get_current_user)
):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can update share settings")

    update_data = {
        "share_settings.enabled": settings.enabled,
        "share_settings.total_shares": settings.total_shares,
        "share_settings.share_price": settings.share_price,
        "share_settings.min_shares_per_member": settings.min_shares_per_member,
        "share_settings.max_shares_per_member": settings.max_shares_per_member,
        "share_settings.allow_share_transfer": settings.allow_share_transfer
    }

    await db.chamas.update_one(
        {"_id": ObjectId(chama_id)},
        {"$set": update_data}
    )

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "action": "update_share_settings",
        "details": f"Updated share settings",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {"message": "Share settings updated successfully"}

@api_router.post("/shares/purchase")
async def purchase_shares(data: SharePurchaseCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can allocate shares")

    # Get chama and settings
    chama = await db.chamas.find_one({"_id": ObjectId(data.chama_id)})
    if not chama:
        raise HTTPException(status_code=404, detail="Chama not found")

    share_settings = chama.get("share_settings", {})
    total_shares = share_settings.get("total_shares", 1000)
    total_allocated = share_settings.get("total_shares_allocated", 0)
    max_shares_per_member = share_settings.get("max_shares_per_member")

    # Verify member exists
    target_member = await db.members.find_one({
        "_id": ObjectId(data.member_id),
        "chama_id": data.chama_id,
        "status": "active"
    })
    if not target_member:
        raise HTTPException(status_code=404, detail="Member not found in this Chama")

    # Check if enough shares available
    if total_allocated + data.quantity > total_shares:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient shares available. Remaining: {total_shares - total_allocated}"
        )

    # Check member's current shares
    current_member_shares = await db.share_purchases.find({
        "chama_id": data.chama_id,
        "member_id": data.member_id,
        "status": "active"
    }).to_list(1000)
    current_quantity = sum(s.get("quantity", 0) for s in current_member_shares)

    # Check max shares limit
    if max_shares_per_member and (current_quantity + data.quantity) > max_shares_per_member:
        raise HTTPException(
            status_code=400,
            detail=f"Member cannot own more than {max_shares_per_member} shares. Currently owns: {current_quantity}"
        )

    # Create share purchase record
    purchase_dict = {
        "chama_id": data.chama_id,
        "member_id": data.member_id,
        "quantity": data.quantity,
        "price_per_share": data.price_per_share,
        "total_amount": data.total_amount,
        "payment_method": data.payment_method,
        "transaction_reference": data.transaction_reference,
        "notes": data.notes,
        "status": "active",
        "purchase_date": datetime.utcnow().isoformat().split('T')[0],
        "allocated_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.share_purchases.insert_one(purchase_dict)

    # Update chama share allocation
    await db.chamas.update_one(
        {"_id": ObjectId(data.chama_id)},
        {
            "$inc": {
                "share_settings.total_shares_allocated": data.quantity,
                "share_settings.total_share_capital": data.total_amount
            }
        }
    )

    # Get member details for logging
    user = await db.users.find_one({"_id": ObjectId(target_member["user_id"])})
    member_name = user["name"] if user else "Unknown"

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "allocate_shares",
        "details": f"Allocated {data.quantity} shares to {member_name} for KES {data.total_amount:,.2f}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "purchase_id": str(result.inserted_id),
        "message": "Shares purchased successfully"
    }

@api_router.get("/shares/purchases/{chama_id}")
async def get_share_purchases(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    purchases = await db.share_purchases.find({
        "chama_id": chama_id,
        "status": "active"
    }).sort("purchase_date", -1).to_list(1000)

    result = []
    for purchase in purchases:
        # Get member details
        member_doc = await db.members.find_one({"_id": ObjectId(purchase["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(purchase["_id"]),
            "member_id": purchase["member_id"],
            "member_name": member_name,
            "quantity": purchase["quantity"],
            "price_per_share": purchase["price_per_share"],
            "total_amount": purchase["total_amount"],
            "payment_method": purchase["payment_method"],
            "transaction_reference": purchase.get("transaction_reference"),
            "purchase_date": purchase["purchase_date"],
            "notes": purchase.get("notes"),
            "created_at": purchase["created_at"]
        })

    return result

@api_router.get("/shares/shareholders/{chama_id}")
async def get_shareholders(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get all active members
    members = await db.members.find({
        "chama_id": chama_id,
        "status": "active"
    }).to_list(1000)

    # Get share settings
    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    share_settings = chama.get("share_settings", {})
    total_shares = share_settings.get("total_shares_allocated", 0)
    share_price = share_settings.get("share_price", 5000.0)
    nav_per_share = share_settings.get("nav_per_share", share_price)

    result = []
    for m in members:
        # Get member's shares
        purchases = await db.share_purchases.find({
            "chama_id": chama_id,
            "member_id": str(m["_id"]),
            "status": "active"
        }).to_list(1000)

        member_shares = sum(p.get("quantity", 0) for p in purchases)

        if member_shares > 0:
            # Get user details
            user = await db.users.find_one({"_id": ObjectId(m["user_id"])})
            member_name = user["name"] if user else "Unknown"
            profile_picture = user.get("profile_picture") if user else None

            percentage = (member_shares / total_shares * 100) if total_shares > 0 else 0
            current_value = member_shares * nav_per_share
            total_invested = sum(p.get("total_amount", 0) for p in purchases)

            result.append({
                "member_id": str(m["_id"]),
                "user_id": str(m["user_id"]),
                "name": member_name,
                "profile_picture": profile_picture,
                "shares": member_shares,
                "percentage": round(percentage, 2),
                "total_invested": total_invested,
                "current_value": current_value,
                "unrealized_gain": current_value - total_invested
            })

    # Sort by shares descending
    result.sort(key=lambda x: x["shares"], reverse=True)

    return result

@api_router.get("/shares/my-shares/{chama_id}")
async def get_my_shares(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get my share purchases
    purchases = await db.share_purchases.find({
        "chama_id": chama_id,
        "member_id": str(member["_id"]),
        "status": "active"
    }).to_list(1000)

    total_shares = sum(p.get("quantity", 0) for p in purchases)
    total_invested = sum(p.get("total_amount", 0) for p in purchases)

    # Get chama settings
    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    share_settings = chama.get("share_settings", {})
    chama_total_shares = share_settings.get("total_shares_allocated", 0)
    nav_per_share = share_settings.get("nav_per_share", share_settings.get("share_price", 5000.0))

    current_value = total_shares * nav_per_share
    percentage = (total_shares / chama_total_shares * 100) if chama_total_shares > 0 else 0

    # Get dividend history
    dividends = await db.dividends.find({
        "chama_id": chama_id,
        f"distributions.{str(member['_id'])}": {"$exists": True}
    }).sort("declaration_date", -1).to_list(100)

    total_dividends_received = 0
    dividend_history = []

    for div in dividends:
        distributions = div.get("distributions", {})
        member_distribution = distributions.get(str(member["_id"]), {})
        amount = member_distribution.get("amount", 0)
        total_dividends_received += amount

        dividend_history.append({
            "id": str(div["_id"]),
            "declaration_date": div["declaration_date"],
            "payment_date": div["payment_date"],
            "dividend_per_share": div["dividend_per_share"],
            "shares_held": member_distribution.get("shares_held", 0),
            "amount_received": amount,
            "status": member_distribution.get("status", "pending")
        })

    return {
        "total_shares": total_shares,
        "total_invested": total_invested,
        "current_value": current_value,
        "unrealized_gain": current_value - total_invested,
        "roi_percentage": ((current_value - total_invested) / total_invested * 100) if total_invested > 0 else 0,
        "ownership_percentage": round(percentage, 2),
        "total_dividends_received": total_dividends_received,
        "purchases": [
            {
                "id": str(p["_id"]),
                "quantity": p["quantity"],
                "price_per_share": p["price_per_share"],
                "total_amount": p["total_amount"],
                "purchase_date": p["purchase_date"],
                "payment_method": p["payment_method"],
                "transaction_reference": p.get("transaction_reference"),
                "notes": p.get("notes")
            }
            for p in purchases
        ],
        "dividend_history": dividend_history
    }

@api_router.post("/shares/transfer")
async def transfer_shares(data: ShareTransferCreate, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can facilitate share transfers")

    # Check if transfers are allowed
    chama = await db.chamas.find_one({"_id": ObjectId(data.chama_id)})
    share_settings = chama.get("share_settings", {})
    if not share_settings.get("allow_share_transfer", True):
        raise HTTPException(status_code=400, detail="Share transfers are not allowed for this Chama")

    # Verify both members exist
    from_member = await db.members.find_one({
        "_id": ObjectId(data.from_member_id),
        "chama_id": data.chama_id,
        "status": "active"
    })
    to_member = await db.members.find_one({
        "_id": ObjectId(data.to_member_id),
        "chama_id": data.chama_id,
        "status": "active"
    })

    if not from_member or not to_member:
        raise HTTPException(status_code=404, detail="One or both members not found")

    # Check if from_member has enough shares
    from_purchases = await db.share_purchases.find({
        "chama_id": data.chama_id,
        "member_id": data.from_member_id,
        "status": "active"
    }).to_list(1000)
    from_shares = sum(p.get("quantity", 0) for p in from_purchases)

    if from_shares < data.quantity:
        raise HTTPException(
            status_code=400,
            detail=f"Insufficient shares. Member has {from_shares} shares, trying to transfer {data.quantity}"
        )

    # Create transfer record
    transfer_dict = {
        "chama_id": data.chama_id,
        "from_member_id": data.from_member_id,
        "to_member_id": data.to_member_id,
        "quantity": data.quantity,
        "price_per_share": data.price_per_share,
        "total_amount": data.total_amount,
        "reason": data.reason,
        "notes": data.notes,
        "transfer_date": datetime.utcnow().isoformat().split('T')[0],
        "processed_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.share_transfers.insert_one(transfer_dict)

    # Deduct from sender - mark oldest purchases as transferred
    remaining_to_transfer = data.quantity
    for purchase in sorted(from_purchases, key=lambda x: x["purchase_date"]):
        if remaining_to_transfer <= 0:
            break

        purchase_qty = purchase.get("quantity", 0)
        if purchase_qty <= remaining_to_transfer:
            # Transfer entire purchase
            await db.share_purchases.update_one(
                {"_id": purchase["_id"]},
                {"$set": {"status": "transferred", "transferred_to": data.to_member_id, "transfer_id": str(result.inserted_id)}}
            )
            remaining_to_transfer -= purchase_qty
        else:
            # Partial transfer - split the purchase
            transfer_qty = remaining_to_transfer
            keep_qty = purchase_qty - transfer_qty

            # Update original purchase to reduce quantity
            await db.share_purchases.update_one(
                {"_id": purchase["_id"]},
                {"$set": {"quantity": keep_qty}}
            )

            # Create transferred portion
            transferred_purchase = purchase.copy()
            transferred_purchase["_id"] = ObjectId()
            transferred_purchase["quantity"] = transfer_qty
            transferred_purchase["status"] = "transferred"
            transferred_purchase["transferred_to"] = data.to_member_id
            transferred_purchase["transfer_id"] = str(result.inserted_id)
            await db.share_purchases.insert_one(transferred_purchase)

            remaining_to_transfer = 0

    # Add to receiver - create new purchase record
    new_purchase = {
        "chama_id": data.chama_id,
        "member_id": data.to_member_id,
        "quantity": data.quantity,
        "price_per_share": data.price_per_share or share_settings.get("share_price", 5000.0),
        "total_amount": data.total_amount or (data.quantity * share_settings.get("share_price", 5000.0)),
        "payment_method": "transfer",
        "transaction_reference": f"TRANSFER-{str(result.inserted_id)[:8]}",
        "notes": f"Received via transfer from member {data.from_member_id}",
        "status": "active",
        "purchase_date": datetime.utcnow().isoformat().split('T')[0],
        "allocated_by": str(current_user["_id"]),
        "transfer_id": str(result.inserted_id),
        "created_at": datetime.utcnow().isoformat()
    }
    await db.share_purchases.insert_one(new_purchase)

    # Get member names for logging
    from_user = await db.users.find_one({"_id": ObjectId(from_member["user_id"])})
    to_user = await db.users.find_one({"_id": ObjectId(to_member["user_id"])})
    from_name = from_user["name"] if from_user else "Unknown"
    to_name = to_user["name"] if to_user else "Unknown"

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "transfer_shares",
        "details": f"Transferred {data.quantity} shares from {from_name} to {to_name}",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "transfer_id": str(result.inserted_id),
        "message": "Shares transferred successfully"
    }

@api_router.get("/shares/transfers/{chama_id}")
async def get_share_transfers(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    transfers = await db.share_transfers.find({
        "chama_id": chama_id
    }).sort("transfer_date", -1).to_list(1000)

    result = []
    for transfer in transfers:
        # Get member details
        from_member = await db.members.find_one({"_id": ObjectId(transfer["from_member_id"])})
        to_member = await db.members.find_one({"_id": ObjectId(transfer["to_member_id"])})

        from_name = "Unknown"
        to_name = "Unknown"

        if from_member:
            from_user = await db.users.find_one({"_id": ObjectId(from_member["user_id"])})
            from_name = from_user["name"] if from_user else "Unknown"

        if to_member:
            to_user = await db.users.find_one({"_id": ObjectId(to_member["user_id"])})
            to_name = to_user["name"] if to_user else "Unknown"

        result.append({
            "id": str(transfer["_id"]),
            "from_member_id": transfer["from_member_id"],
            "from_member_name": from_name,
            "to_member_id": transfer["to_member_id"],
            "to_member_name": to_name,
            "quantity": transfer["quantity"],
            "price_per_share": transfer.get("price_per_share"),
            "total_amount": transfer.get("total_amount"),
            "transfer_date": transfer["transfer_date"],
            "reason": transfer.get("reason"),
            "notes": transfer.get("notes"),
            "created_at": transfer["created_at"]
        })

    return result

@api_router.post("/shares/declare-dividend")
async def declare_dividend(data: DividendDeclaration, current_user: dict = Depends(get_current_user)):
    # Verify admin
    member = await db.members.find_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member or member["role"] != "admin":
        raise HTTPException(status_code=403, detail="Only admins can declare dividends")

    # Get all shareholders
    shareholders = await get_shareholders(data.chama_id, current_user)

    # Calculate distributions
    distributions = {}
    for shareholder in shareholders:
        member_id = shareholder["member_id"]
        shares = shareholder["shares"]
        amount = shares * data.dividend_per_share

        distributions[member_id] = {
            "shares_held": shares,
            "amount": amount,
            "status": "pending"
        }

    # Create dividend record
    dividend_dict = {
        "chama_id": data.chama_id,
        "total_dividend_amount": data.total_dividend_amount,
        "dividend_per_share": data.dividend_per_share,
        "declaration_date": data.declaration_date,
        "payment_date": data.payment_date,
        "financial_year": data.financial_year,
        "notes": data.notes,
        "distributions": distributions,
        "declared_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }

    result = await db.dividends.insert_one(dividend_dict)

    # Audit log
    await db.audit_logs.insert_one({
        "chama_id": data.chama_id,
        "user_id": str(current_user["_id"]),
        "action": "declare_dividend",
        "details": f"Declared dividend of KES {data.dividend_per_share:,.2f} per share (Total: KES {data.total_dividend_amount:,.2f})",
        "timestamp": datetime.utcnow().isoformat()
    })

    return {
        "dividend_id": str(result.inserted_id),
        "message": "Dividend declared successfully",
        "distributions_count": len(distributions)
    }

@api_router.get("/shares/dividends/{chama_id}")
async def get_dividends(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    dividends = await db.dividends.find({
        "chama_id": chama_id
    }).sort("declaration_date", -1).to_list(100)

    result = []
    for div in dividends:
        result.append({
            "id": str(div["_id"]),
            "total_dividend_amount": div["total_dividend_amount"],
            "dividend_per_share": div["dividend_per_share"],
            "declaration_date": div["declaration_date"],
            "payment_date": div["payment_date"],
            "financial_year": div["financial_year"],
            "notes": div.get("notes"),
            "distributions_count": len(div.get("distributions", {})),
            "created_at": div["created_at"]
        })

    return result

@api_router.get("/shares/analytics/{chama_id}")
async def get_share_analytics(chama_id: str, current_user: dict = Depends(get_current_user)):
    # Verify membership
    member = await db.members.find_one({
        "chama_id": chama_id,
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not a member of this Chama")

    # Get chama and settings
    chama = await db.chamas.find_one({"_id": ObjectId(chama_id)})
    share_settings = chama.get("share_settings", {})

    # Get all purchases
    purchases = await db.share_purchases.find({
        "chama_id": chama_id,
        "status": "active"
    }).to_list(10000)

    # Get all dividends
    dividends = await db.dividends.find({"chama_id": chama_id}).to_list(1000)

    total_shares_allocated = sum(p.get("quantity", 0) for p in purchases)
    total_share_capital = sum(p.get("total_amount", 0) for p in purchases)
    total_dividends_paid = sum(d.get("total_dividend_amount", 0) for d in dividends)

    # Calculate NAV
    # Get chama balance
    contributions = await db.contributions.find({
        "chama_id": chama_id,
        "status": "paid"
    }).to_list(10000)
    total_contributions = sum(c["amount"] for c in contributions)

    loans = await db.loans.find({
        "chama_id": chama_id,
        "status": "approved"
    }).to_list(10000)
    total_loans = sum(l["amount"] for l in loans)

    repayments = await db.repayments.find({}).to_list(10000)
    loan_ids = [str(l["_id"]) for l in loans]
    total_repaid = sum(r["amount"] for r in repayments if r["loan_id"] in loan_ids)

    # Get investments
    investments = await db.investments.find({"chama_id": chama_id}).to_list(1000)
    total_investment_value = sum(inv["current_value"] for inv in investments)

    net_asset_value = total_contributions - total_loans + total_repaid + total_investment_value - total_dividends_paid
    nav_per_share = (net_asset_value / total_shares_allocated) if total_shares_allocated > 0 else share_settings.get("share_price", 5000.0)

    # Update NAV in chama settings
    await db.chamas.update_one(
        {"_id": ObjectId(chama_id)},
        {"$set": {"share_settings.nav_per_share": nav_per_share}}
    )

    # Count shareholders
    unique_shareholders = set(p["member_id"] for p in purchases)

    return {
        "total_shares": share_settings.get("total_shares", 1000),
        "total_shares_allocated": total_shares_allocated,
        "total_shares_available": share_settings.get("total_shares", 1000) - total_shares_allocated,
        "allocation_percentage": (total_shares_allocated / share_settings.get("total_shares", 1000) * 100) if share_settings.get("total_shares", 1000) > 0 else 0,
        "total_shareholders": len(unique_shareholders),
        "total_share_capital": total_share_capital,
        "share_price": share_settings.get("share_price", 5000.0),
        "net_asset_value": net_asset_value,
        "nav_per_share": nav_per_share,
        "nav_growth": ((nav_per_share - share_settings.get("share_price", 5000.0)) / share_settings.get("share_price", 5000.0) * 100) if share_settings.get("share_price", 5000.0) > 0 else 0,
        "total_dividends_declared": len(dividends),
        "total_dividends_paid": total_dividends_paid
    }

app.include_router(api_router)

# Health check endpoint
@app.get("/health")
async def health_check():
    try:
        # Test database connection
        await db.command("ping")
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        logger.error(f"Health check failed: {str(e)}")
        return JSONResponse(
            status_code=503,
            content={"status": "unhealthy", "database": "disconnected", "error": str(e)}
        )

@app.get("/")
async def root():
    return {
        "message": "ChamaKe API",
        "version": "1.0.0",
        "docs": "/docs",
        "health": "/health"
    }

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Auto-Contribution Scheduler
async def auto_generate_contributions_task():
    """Background task that runs periodically to auto-generate monthly contributions"""
    while True:
        try:
            logger.info("Running auto-contribution generation task...")

            # Get all chamas with auto-contribution enabled
            chamas = await db.chamas.find({
                "auto_contribution_enabled": True,
                "auto_create_contributions": True
            }).to_list(1000)

            logger.info(f"Found {len(chamas)} chamas with auto-contribution enabled")

            for chama in chamas:
                try:
                    chama_id = str(chama["_id"])
                    contribution_amount = chama.get("contribution_amount", 0)
                    contribution_day = chama.get("contribution_deadline", 1)

                    if contribution_amount <= 0:
                        logger.warning(f"Skipping chama {chama_id}: Invalid contribution amount")
                        continue

                    # Check if we should generate contributions for this chama
                    today = datetime.utcnow()
                    current_day = today.day

                    # Generate contributions on the specified day of the month
                    if current_day == contribution_day:
                        # Check last run to avoid duplicate generation
                        last_run = chama.get("last_auto_contribution_run")
                        if last_run:
                            last_run_date = datetime.fromisoformat(last_run)
                            # Only run once per month
                            if last_run_date.month == today.month and last_run_date.year == today.year:
                                logger.info(f"Skipping chama {chama_id}: Already generated for this month")
                                continue

                        # Calculate next month's due date
                        next_month = today.month + 1 if today.month < 12 else 1
                        next_year = today.year if today.month < 12 else today.year + 1

                        # Ensure the day is valid for the month
                        max_day = calendar.monthrange(next_year, next_month)[1]
                        safe_day = min(contribution_day, max_day)

                        due_date = datetime(next_year, next_month, safe_day).isoformat().split('T')[0]

                        # Check if contributions already exist for this period
                        existing = await db.contributions.find_one({
                            "chama_id": chama_id,
                            "due_date": due_date,
                            "is_historical": {"$ne": True}
                        })

                        if existing:
                            logger.info(f"Skipping chama {chama_id}: Contributions already exist for {due_date}")
                            continue

                        # Get all active members
                        members = await db.members.find({
                            "chama_id": chama_id,
                            "status": "active"
                        }).to_list(1000)

                        # Get fine amount
                        fine_amount = chama.get("fine_amount", 0.0)

                        # Create contributions for all members (base amount only)
                        contributions_to_insert = []
                        members_with_penalties = 0

                        for m in members:
                            member_id = str(m["_id"])

                            # Check for unpaid contributions for this member
                            unpaid_contributions = await db.contributions.find({
                                "chama_id": chama_id,
                                "member_id": member_id,
                                "status": {"$in": ["pending", "pending_verification"]},
                                "due_date": {"$lt": due_date},  # Only past contributions
                            }).to_list(1000)

                            has_unpaid = unpaid_contributions and len(unpaid_contributions) > 0

                            # Current month contribution - always just the base amount (no consolidation)
                            contribution_dict = {
                                "chama_id": chama_id,
                                "member_id": member_id,
                                "amount": contribution_amount,
                                "due_date": due_date,
                                "status": "pending",
                                "paid_date": None,
                                "is_historical": False,
                                "auto_generated": True,
                                "created_at": datetime.utcnow().isoformat()
                            }

                            # Add penalty information if member has unpaid contributions (penalty tracked separately)
                            if has_unpaid and fine_amount > 0:
                                total_unpaid = sum(c["amount"] for c in unpaid_contributions)
                                contribution_dict["penalty_info"] = {
                                    "has_penalty": True,
                                    "penalty_amount": fine_amount,
                                    "reason": "Late payment penalty for unpaid contributions",
                                    "unpaid_count": len(unpaid_contributions),
                                    "total_unpaid_balance": total_unpaid
                                }
                                members_with_penalties += 1
                            else:
                                contribution_dict["penalty_info"] = {
                                    "has_penalty": False,
                                    "penalty_amount": 0
                                }

                            contributions_to_insert.append(contribution_dict)

                        if contributions_to_insert:
                            result = await db.contributions.insert_many(contributions_to_insert)
                            inserted_count = len(result.inserted_ids)

                            # Update last run timestamp
                            await db.chamas.update_one(
                                {"_id": chama["_id"]},
                                {"$set": {"last_auto_contribution_run": datetime.utcnow().isoformat()}}
                            )

                            # Audit log
                            await db.audit_logs.insert_one({
                                "chama_id": chama_id,
                                "user_id": "system",
                                "action": "auto_generate_contributions",
                                "details": f"Auto-generated {inserted_count} contributions for {due_date} ({members_with_penalties} with penalties)",
                                "timestamp": datetime.utcnow().isoformat()
                            })

                            logger.info(f"Generated {inserted_count} contributions for chama {chama_id} (due: {due_date}, {members_with_penalties} with penalties)")

                except Exception as e:
                    logger.error(f"Error processing chama {chama.get('_id')}: {str(e)}", exc_info=True)
                    continue

            logger.info("Auto-contribution generation task completed")

        except Exception as e:
            logger.error(f"Error in auto-contribution task: {str(e)}", exc_info=True)

        # Run this task once per day (every 24 hours)
        await asyncio.sleep(86400)  # 24 hours in seconds

# Manual trigger endpoint for testing/admin use
@api_router.post("/system/trigger-auto-contributions")
async def trigger_auto_contributions(background_tasks: BackgroundTasks):
    """Manually trigger the auto-contribution generation (admin/system use)"""
    background_tasks.add_task(auto_generate_contributions_once)
    return {
        "message": "Auto-contribution generation triggered",
        "status": "running"
    }

async def auto_generate_contributions_once():
    """One-time execution of auto-contribution generation"""
    try:
        logger.info("Manual trigger: Running auto-contribution generation...")

        chamas = await db.chamas.find({
            "auto_contribution_enabled": True,
            "auto_create_contributions": True
        }).to_list(1000)

        total_generated = 0

        for chama in chamas:
            try:
                chama_id = str(chama["_id"])
                contribution_amount = chama.get("contribution_amount", 0)
                contribution_day = chama.get("contribution_deadline", 1)

                if contribution_amount <= 0:
                    continue

                today = datetime.utcnow()
                next_month = today.month + 1 if today.month < 12 else 1
                next_year = today.year if today.month < 12 else today.year + 1

                max_day = calendar.monthrange(next_year, next_month)[1]
                safe_day = min(contribution_day, max_day)
                due_date = datetime(next_year, next_month, safe_day).isoformat().split('T')[0]

                existing = await db.contributions.find_one({
                    "chama_id": chama_id,
                    "due_date": due_date,
                    "is_historical": {"$ne": True}
                })

                if existing:
                    continue

                members = await db.members.find({
                    "chama_id": chama_id,
                    "status": "active"
                }).to_list(1000)

                # Get fine amount
                fine_amount = chama.get("fine_amount", 0.0)

                contributions_to_insert = []
                members_with_penalties = 0

                for m in members:
                    member_id = str(m["_id"])

                    # Check for unpaid contributions for this member
                    unpaid_contributions = await db.contributions.find({
                        "chama_id": chama_id,
                        "member_id": member_id,
                        "status": {"$in": ["pending", "pending_verification"]},
                        "due_date": {"$lt": due_date},  # Only past contributions
                    }).to_list(1000)

                    has_unpaid = unpaid_contributions and len(unpaid_contributions) > 0

                    # Current month contribution - always just the base amount (no consolidation)
                    contribution_dict = {
                        "chama_id": chama_id,
                        "member_id": member_id,
                        "amount": contribution_amount,
                        "due_date": due_date,
                        "status": "pending",
                        "paid_date": None,
                        "is_historical": False,
                        "auto_generated": True,
                        "created_at": datetime.utcnow().isoformat()
                    }

                    # Add penalty information if member has unpaid contributions (penalty tracked separately)
                    if has_unpaid and fine_amount > 0:
                        total_unpaid = sum(c["amount"] for c in unpaid_contributions)
                        contribution_dict["penalty_info"] = {
                            "has_penalty": True,
                            "penalty_amount": fine_amount,
                            "reason": "Late payment penalty for unpaid contributions",
                            "unpaid_count": len(unpaid_contributions),
                            "total_unpaid_balance": total_unpaid
                        }
                        members_with_penalties += 1
                    else:
                        contribution_dict["penalty_info"] = {
                            "has_penalty": False,
                            "penalty_amount": 0
                        }

                    contributions_to_insert.append(contribution_dict)

                if contributions_to_insert:
                    result = await db.contributions.insert_many(contributions_to_insert)
                    inserted_count = len(result.inserted_ids)
                    total_generated += inserted_count

                    await db.chamas.update_one(
                        {"_id": chama["_id"]},
                        {"$set": {"last_auto_contribution_run": datetime.utcnow().isoformat()}}
                    )

                    await db.audit_logs.insert_one({
                        "chama_id": chama_id,
                        "user_id": "system",
                        "action": "auto_generate_contributions",
                        "details": f"Auto-generated {inserted_count} contributions for {due_date} ({members_with_penalties} with penalties)",
                        "timestamp": datetime.utcnow().isoformat()
                    })

            except Exception as e:
                logger.error(f"Error processing chama: {str(e)}", exc_info=True)
                continue

        logger.info(f"Manual trigger completed: Generated {total_generated} total contributions")

    except Exception as e:
        logger.error(f"Error in manual auto-contribution trigger: {str(e)}", exc_info=True)

@app.on_event("startup")
async def startup_event():
    """Start background tasks on app startup"""
    logger.info("Starting ChamaKe API...")
    # Start the auto-contribution scheduler in the background
    asyncio.create_task(auto_generate_contributions_task())
    logger.info("Auto-contribution scheduler started")

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
