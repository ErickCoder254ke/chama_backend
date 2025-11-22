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
    
    outstanding_loans = total_loans_issued - total_repaid
    current_balance = total_contributions - total_loans_issued + total_repaid
    
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
