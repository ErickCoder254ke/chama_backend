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
    repayment_period: int  # days
    interest_rate: float = 10.0

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
    # Find all chamas where user is a member
    members = await db.members.find({
        "user_id": str(current_user["_id"]),
        "status": "active"
    }).to_list(100)
    
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
    
    members = await db.members.find({"chama_id": chama_id, "status": "active"}).to_list(1000)
    
    result = []
    for m in members:
        user = await db.users.find_one({"_id": ObjectId(m["user_id"])})
        if user:
            result.append({
                "member_id": str(m["_id"]),
                "user_id": str(user["_id"]),
                "name": user["name"],
                "phone": user["phone"],
                "profile_picture": user.get("profile_picture"),
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
    
    contributions = await db.contributions.find({"chama_id": chama_id}).to_list(1000)
    
    result = []
    for c in contributions:
        # Get member details - member_id is actually the member document _id
        member_doc = await db.members.find_one({"_id": ObjectId(c["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
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

    # Get contributions for this member
    contributions = await db.contributions.find({
        "chama_id": chama_id,
        "member_id": str(member["_id"])
    }).to_list(1000)

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
            "receipt_image": None
        }

        # Get payment details if exists
        if c.get("payment_id"):
            payment = await db.payments.find_one({"_id": ObjectId(c["payment_id"])})
            if payment:
                contribution_data.update({
                    "payment_method": payment.get("payment_method"),
                    "transaction_reference": payment.get("transaction_reference"),
                    "receipt_image": payment.get("receipt_image"),
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

    # Get pending payments
    payments = await db.payments.find({
        "chama_id": chama_id,
        "status": "pending_verification"
    }).to_list(1000)

    result = []
    for p in payments:
        # Get member details
        member_doc = await db.members.find_one({"_id": ObjectId(p["member_id"])})
        member_name = "Unknown"
        if member_doc:
            user = await db.users.find_one({"_id": ObjectId(member_doc["user_id"])})
            member_name = user["name"] if user else "Unknown"

        result.append({
            "id": str(p["_id"]),
            "contribution_id": p["contribution_id"],
            "member_name": member_name,
            "amount": p["amount"],
            "payment_method": p["payment_method"],
            "transaction_reference": p.get("transaction_reference"),
            "receipt_image": p.get("receipt_image"),
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

    # Create contributions for all members with arrears tracking
    contributions_to_insert = []
    arrears_created = 0

    for m in members:
        member_id = str(m["_id"])

        # Check for unpaid contributions for this member (exclude consolidated)
        unpaid_contributions = await db.contributions.find({
            "chama_id": chama_id,
            "member_id": member_id,
            "status": {"$in": ["pending", "pending_verification"]},
            "due_date": {"$lt": due_date},  # Only past contributions
            "consolidated_into_arrears": {"$ne": True}  # Exclude already consolidated
        }).to_list(1000)

        if unpaid_contributions and len(unpaid_contributions) > 0:
            # Member has unpaid contributions - create arrears record
            total_unpaid = sum(c["amount"] for c in unpaid_contributions)

            # Build arrears breakdown
            arrears_breakdown = {
                "previous_unpaid": total_unpaid,
                "current_month_contribution": contribution_amount,
                "fine": fine_amount,
                "total_arrears": total_unpaid + contribution_amount + fine_amount,
                "unpaid_periods": [
                    {
                        "contribution_id": str(c["_id"]),
                        "due_date": c["due_date"],
                        "amount": c["amount"]
                    }
                    for c in unpaid_contributions
                ]
            }

            contribution_dict = {
                "chama_id": chama_id,
                "member_id": member_id,
                "amount": total_unpaid + contribution_amount + fine_amount,
                "due_date": due_date,
                "status": "pending",
                "paid_date": None,
                "is_historical": False,
                "auto_generated": True,
                "is_arrears": True,
                "arrears_breakdown": arrears_breakdown,
                "created_at": datetime.utcnow().isoformat()
            }
            arrears_created += 1

            # Mark old unpaid contributions as consolidated to prevent double-counting
            unpaid_contribution_ids = [c["_id"] for c in unpaid_contributions]
            if unpaid_contribution_ids:
                await db.contributions.update_many(
                    {"_id": {"$in": unpaid_contribution_ids}},
                    {"$set": {
                        "consolidated_into_arrears": True,
                        "consolidated_date": datetime.utcnow().isoformat(),
                        "arrears_contribution_due_date": due_date
                    }}
                )
        else:
            # No unpaid contributions - create normal contribution
            contribution_dict = {
                "chama_id": chama_id,
                "member_id": member_id,
                "amount": contribution_amount,
                "due_date": due_date,
                "status": "pending",
                "paid_date": None,
                "is_historical": False,
                "auto_generated": True,
                "is_arrears": False,
                "created_at": datetime.utcnow().isoformat()
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
            "details": f"Generated {inserted_count} contributions for {due_date} ({arrears_created} with arrears)",
            "timestamp": datetime.utcnow().isoformat()
        })

        return {
            "message": f"Successfully generated {inserted_count} monthly contributions",
            "count": inserted_count,
            "arrears_count": arrears_created,
            "due_date": due_date,
            "amount": contribution_amount
        }

    return {"message": "No contributions to generate", "count": 0}

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
    
    loan_dict = {
        "chama_id": data.chama_id,
        "member_id": str(member["_id"]),
        "user_id": str(current_user["_id"]),
        "amount": data.amount,
        "interest_rate": data.interest_rate,
        "reason": data.reason,
        "repayment_period": data.repayment_period,
        "status": "pending",
        "approved_date": None,
        "outstanding_balance": data.amount,
        "created_at": datetime.utcnow().isoformat()
    }
    
    result = await db.loans.insert_one(loan_dict)
    return {"loan_id": str(result.inserted_id), "message": "Loan request submitted"}

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
    
    loans = await db.loans.find({"chama_id": chama_id}).to_list(1000)
    
    result = []
    for loan in loans:
        user = await db.users.find_one({"_id": ObjectId(loan["user_id"])})
        result.append({
            "id": str(loan["_id"]),
            "member_name": user["name"] if user else "Unknown",
            "amount": loan["amount"],
            "interest_rate": loan["interest_rate"],
            "reason": loan["reason"],
            "status": loan["status"],
            "outstanding_balance": loan.get("outstanding_balance", loan["amount"]),
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
    
    # Verify admin or loan owner
    member = await db.members.find_one({
        "chama_id": loan["chama_id"],
        "user_id": str(current_user["_id"]),
        "status": "active"
    })
    if not member:
        raise HTTPException(status_code=403, detail="Not authorized")
    
    # Create repayment record
    repayment_dict = {
        "loan_id": data.loan_id,
        "amount": data.amount,
        "payment_date": datetime.utcnow().isoformat(),
        "confirmed_by": str(current_user["_id"]),
        "created_at": datetime.utcnow().isoformat()
    }
    await db.repayments.insert_one(repayment_dict)
    
    # Update loan balance
    new_balance = loan.get("outstanding_balance", loan["amount"]) - data.amount
    await db.loans.update_one(
        {"_id": ObjectId(data.loan_id)},
        {"$set": {"outstanding_balance": max(0, new_balance)}}
    )
    
    return {"message": "Repayment recorded", "remaining_balance": max(0, new_balance)}

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
    
    repayments = await db.repayments.find({}).to_list(10000)
    loan_ids = [str(l["_id"]) for l in loans]
    total_repaid = sum(r["amount"] for r in repayments if r["loan_id"] in loan_ids)
    
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

                        # Create contributions for all members with arrears tracking
                        contributions_to_insert = []
                        arrears_created = 0

                        for m in members:
                            member_id = str(m["_id"])

                            # Check for unpaid contributions for this member (exclude consolidated)
                            unpaid_contributions = await db.contributions.find({
                                "chama_id": chama_id,
                                "member_id": member_id,
                                "status": {"$in": ["pending", "pending_verification"]},
                                "due_date": {"$lt": due_date},  # Only past contributions
                                "consolidated_into_arrears": {"$ne": True}  # Exclude already consolidated
                            }).to_list(1000)

                            if unpaid_contributions and len(unpaid_contributions) > 0:
                                # Member has unpaid contributions - create arrears record
                                total_unpaid = sum(c["amount"] for c in unpaid_contributions)

                                # Build arrears breakdown
                                arrears_breakdown = {
                                    "previous_unpaid": total_unpaid,
                                    "current_month_contribution": contribution_amount,
                                    "fine": fine_amount,
                                    "total_arrears": total_unpaid + contribution_amount + fine_amount,
                                    "unpaid_periods": [
                                        {
                                            "contribution_id": str(c["_id"]),
                                            "due_date": c["due_date"],
                                            "amount": c["amount"]
                                        }
                                        for c in unpaid_contributions
                                    ]
                                }

                                contribution_dict = {
                                    "chama_id": chama_id,
                                    "member_id": member_id,
                                    "amount": total_unpaid + contribution_amount + fine_amount,
                                    "due_date": due_date,
                                    "status": "pending",
                                    "paid_date": None,
                                    "is_historical": False,
                                    "auto_generated": True,
                                    "is_arrears": True,
                                    "arrears_breakdown": arrears_breakdown,
                                    "created_at": datetime.utcnow().isoformat()
                                }
                                arrears_created += 1

                                # Mark old unpaid contributions as consolidated to prevent double-counting
                                unpaid_contribution_ids = [c["_id"] for c in unpaid_contributions]
                                if unpaid_contribution_ids:
                                    await db.contributions.update_many(
                                        {"_id": {"$in": unpaid_contribution_ids}},
                                        {"$set": {
                                            "consolidated_into_arrears": True,
                                            "consolidated_date": datetime.utcnow().isoformat(),
                                            "arrears_contribution_due_date": due_date
                                        }}
                                    )
                            else:
                                # No unpaid contributions - create normal contribution
                                contribution_dict = {
                                    "chama_id": chama_id,
                                    "member_id": member_id,
                                    "amount": contribution_amount,
                                    "due_date": due_date,
                                    "status": "pending",
                                    "paid_date": None,
                                    "is_historical": False,
                                    "auto_generated": True,
                                    "is_arrears": False,
                                    "created_at": datetime.utcnow().isoformat()
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
                                "details": f"Auto-generated {inserted_count} contributions for {due_date} ({arrears_created} with arrears)",
                                "timestamp": datetime.utcnow().isoformat()
                            })

                            logger.info(f"Generated {inserted_count} contributions for chama {chama_id} (due: {due_date}, {arrears_created} with arrears)")

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
                arrears_created = 0

                for m in members:
                    member_id = str(m["_id"])

                    # Check for unpaid contributions for this member (exclude consolidated)
                    unpaid_contributions = await db.contributions.find({
                        "chama_id": chama_id,
                        "member_id": member_id,
                        "status": {"$in": ["pending", "pending_verification"]},
                        "due_date": {"$lt": due_date},  # Only past contributions
                        "consolidated_into_arrears": {"$ne": True}  # Exclude already consolidated
                    }).to_list(1000)

                    if unpaid_contributions and len(unpaid_contributions) > 0:
                        # Member has unpaid contributions - create arrears record
                        total_unpaid = sum(c["amount"] for c in unpaid_contributions)

                        # Build arrears breakdown
                        arrears_breakdown = {
                            "previous_unpaid": total_unpaid,
                            "current_month_contribution": contribution_amount,
                            "fine": fine_amount,
                            "total_arrears": total_unpaid + contribution_amount + fine_amount,
                            "unpaid_periods": [
                                {
                                    "contribution_id": str(c["_id"]),
                                    "due_date": c["due_date"],
                                    "amount": c["amount"]
                                }
                                for c in unpaid_contributions
                            ]
                        }

                        contribution_dict = {
                            "chama_id": chama_id,
                            "member_id": member_id,
                            "amount": total_unpaid + contribution_amount + fine_amount,
                            "due_date": due_date,
                            "status": "pending",
                            "paid_date": None,
                            "is_historical": False,
                            "auto_generated": True,
                            "is_arrears": True,
                            "arrears_breakdown": arrears_breakdown,
                            "created_at": datetime.utcnow().isoformat()
                        }
                        arrears_created += 1

                        # Mark old unpaid contributions as consolidated to prevent double-counting
                        unpaid_contribution_ids = [c["_id"] for c in unpaid_contributions]
                        if unpaid_contribution_ids:
                            await db.contributions.update_many(
                                {"_id": {"$in": unpaid_contribution_ids}},
                                {"$set": {
                                    "consolidated_into_arrears": True,
                                    "consolidated_date": datetime.utcnow().isoformat(),
                                    "arrears_contribution_due_date": due_date
                                }}
                            )
                    else:
                        # No unpaid contributions - create normal contribution
                        contribution_dict = {
                            "chama_id": chama_id,
                            "member_id": member_id,
                            "amount": contribution_amount,
                            "due_date": due_date,
                            "status": "pending",
                            "paid_date": None,
                            "is_historical": False,
                            "auto_generated": True,
                            "is_arrears": False,
                            "created_at": datetime.utcnow().isoformat()
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
                        "details": f"Auto-generated {inserted_count} contributions for {due_date} ({arrears_created} with arrears)",
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
