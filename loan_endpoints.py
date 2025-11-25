"""
Additional Loan Calculator API Endpoints
To be integrated into server.py
"""

# Add these endpoints after the existing loan endpoints in server.py:

"""
@api_router.get("/loans/{loan_id}/amortization-schedule")
async def get_loan_amortization_schedule(loan_id: str, current_user: dict = Depends(get_current_user)):
    '''Get detailed amortization schedule for a specific loan'''
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
    '''Calculate loan details without creating a loan request'''
    from loan_calculator import (
        validate_loan_parameters,
        generate_amortization_schedule,
        calculate_monthly_payment
    )
    
    # Validate parameters
    validation = validate_loan_parameters(principal, interest_rate, term_months)
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
    '''Check if a proposed loan is affordable based on income'''
    from loan_calculator import calculate_affordability, calculate_monthly_payment
    
    # Calculate monthly payment
    monthly_payment = calculate_monthly_payment(loan_amount, interest_rate, term_months)
    
    # Check affordability
    affordability = calculate_affordability(
        monthly_income=monthly_income,
        existing_monthly_debts=existing_debts,
        proposed_loan_payment=monthly_payment
    )
    
    return {
        "monthly_payment": round(monthly_payment, 2),
        "monthly_income": monthly_income,
        "existing_debts": existing_debts,
        "current_dti": affordability["current_dti"],
        "new_dti": affordability["new_dti"],
        "is_affordable": affordability["is_affordable"],
        "max_affordable_payment": affordability["max_affordable_payment"],
        "recommendation": (
            "This loan is affordable and within recommended debt limits." 
            if affordability["is_affordable"] 
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
    '''Calculate maximum affordable loan based on payment capacity'''
    from loan_calculator import calculate_max_loan_amount
    
    max_loan = calculate_max_loan_amount(
        monthly_payment_capacity=monthly_payment_capacity,
        annual_interest_rate=interest_rate,
        term_in_months=term_months
    )
    
    return {
        "max_loan_amount": round(max_loan, 2),
        "monthly_payment": monthly_payment_capacity,
        "interest_rate": interest_rate,
        "term_months": term_months,
        "total_interest": round(max_loan * (interest_rate / 100) * (term_months / 12), 2),
        "message": f"Based on a monthly payment capacity of KES {monthly_payment_capacity:,.2f}, "
                   f"you can borrow up to KES {max_loan:,.2f} over {term_months} months at {interest_rate}% interest."
    }


@api_router.get("/loans/{loan_id}/payment-schedule-status")
async def get_payment_schedule_status(loan_id: str, current_user: dict = Depends(get_current_user)):
    '''Get payment schedule with status (paid, overdue, upcoming)'''
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
"""
