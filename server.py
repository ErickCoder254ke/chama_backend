from fastapi import FastAPI, APIRouter, HTTPException, Depends, status, Request
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
        await db.payments.update_one(
            {"_id": ObjectId(contribution["payment_id"])},
            {"$set": {
                "verified": data.verified,
                "verified_by": str(current_user["_id"]),
                "admin_notes": data.admin_notes,
                "verified_at": datetime.utcnow().isoformat()
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

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()
