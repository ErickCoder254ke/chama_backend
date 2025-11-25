"""
Loan Calculator Utilities for ChamaKe Backend
Provides comprehensive loan amortization calculations
"""

from datetime import datetime, timedelta
from typing import List, Dict, Optional
from dateutil.relativedelta import relativedelta
import math


class PaymentSchedule:
    def __init__(
        self,
        payment_number: int,
        due_date: str,
        payment: float,
        principal: float,
        interest: float,
        balance: float,
        cumulative_interest: float,
        cumulative_principal: float
    ):
        self.payment_number = payment_number
        self.due_date = due_date
        self.payment = payment
        self.principal = principal
        self.interest = interest
        self.balance = balance
        self.cumulative_interest = cumulative_interest
        self.cumulative_principal = cumulative_principal

    def to_dict(self) -> Dict:
        return {
            "payment_number": self.payment_number,
            "due_date": self.due_date,
            "payment": round(self.payment, 2),
            "principal": round(self.principal, 2),
            "interest": round(self.interest, 2),
            "balance": round(self.balance, 2),
            "cumulative_interest": round(self.cumulative_interest, 2),
            "cumulative_principal": round(self.cumulative_principal, 2),
        }


class LoanSummary:
    def __init__(
        self,
        principal: float,
        total_interest: float,
        total_payment: float,
        monthly_payment: float,
        number_of_payments: int,
        effective_rate: float,
        schedule: List[PaymentSchedule]
    ):
        self.principal = principal
        self.total_interest = total_interest
        self.total_payment = total_payment
        self.monthly_payment = monthly_payment
        self.number_of_payments = number_of_payments
        self.effective_rate = effective_rate
        self.schedule = schedule

    def to_dict(self) -> Dict:
        return {
            "principal": round(self.principal, 2),
            "total_interest": round(self.total_interest, 2),
            "total_payment": round(self.total_payment, 2),
            "monthly_payment": round(self.monthly_payment, 2),
            "number_of_payments": self.number_of_payments,
            "effective_rate": self.effective_rate,
            "schedule": [s.to_dict() for s in self.schedule]
        }


def calculate_monthly_payment(
    principal: float,
    annual_interest_rate: float,
    term_in_months: int
) -> float:
    """
    Calculate monthly payment using amortization formula
    Formula: M = P × [r(1+r)^n] / [(1+r)^n - 1]
    
    Args:
        principal: Loan principal amount
        annual_interest_rate: Annual interest rate as percentage (e.g., 12 for 12%)
        term_in_months: Loan term in months
    
    Returns:
        Monthly payment amount
    """
    if principal <= 0 or term_in_months <= 0:
        return 0.0

    # Handle zero interest rate
    if annual_interest_rate == 0:
        return principal / term_in_months

    monthly_rate = annual_interest_rate / 100 / 12
    number_of_payments = term_in_months

    monthly_payment = (
        principal * (monthly_rate * math.pow(1 + monthly_rate, number_of_payments))
    ) / (math.pow(1 + monthly_rate, number_of_payments) - 1)

    return round(monthly_payment, 2)


def calculate_payment(
    principal: float,
    annual_interest_rate: float,
    term_in_months: int,
    frequency: str = "monthly"
) -> float:
    """
    Calculate payment for different frequencies
    
    Args:
        principal: Loan principal amount
        annual_interest_rate: Annual interest rate as percentage
        term_in_months: Loan term in months
        frequency: Payment frequency ('monthly', 'weekly', 'biweekly')
    
    Returns:
        Payment amount based on frequency
    """
    if principal <= 0 or term_in_months <= 0:
        return 0.0

    if annual_interest_rate == 0:
        total_payments = get_number_of_payments(term_in_months, frequency)
        return principal / total_payments

    periodic_rate = get_periodic_rate(annual_interest_rate, frequency)
    number_of_payments = get_number_of_payments(term_in_months, frequency)

    payment = (
        principal * (periodic_rate * math.pow(1 + periodic_rate, number_of_payments))
    ) / (math.pow(1 + periodic_rate, number_of_payments) - 1)

    return round(payment, 2)


def get_periodic_rate(annual_rate: float, frequency: str) -> float:
    """Get periodic interest rate based on payment frequency"""
    rate = annual_rate / 100
    
    frequency_map = {
        "monthly": rate / 12,
        "weekly": rate / 52,
        "biweekly": rate / 26
    }
    
    return frequency_map.get(frequency, rate / 12)


def get_number_of_payments(term_in_months: int, frequency: str) -> int:
    """Get total number of payments based on term and frequency"""
    frequency_map = {
        "monthly": term_in_months,
        "weekly": math.ceil(term_in_months * 4.33),  # Average weeks per month
        "biweekly": math.ceil(term_in_months * 2.17)  # Average bi-weeks per month
    }
    
    return frequency_map.get(frequency, term_in_months)


def generate_amortization_schedule(
    principal: float,
    annual_interest_rate: float,
    term_in_months: int,
    start_date: Optional[str] = None,
    payment_frequency: str = "monthly"
) -> LoanSummary:
    """
    Generate complete amortization schedule
    
    Args:
        principal: Loan principal amount
        annual_interest_rate: Annual interest rate as percentage
        term_in_months: Loan term in months
        start_date: Loan start date (ISO format), defaults to today
        payment_frequency: Payment frequency ('monthly', 'weekly', 'biweekly')
    
    Returns:
        LoanSummary object with complete schedule
    """
    payment = calculate_payment(principal, annual_interest_rate, term_in_months, payment_frequency)
    periodic_rate = get_periodic_rate(annual_interest_rate, payment_frequency)
    number_of_payments = get_number_of_payments(term_in_months, payment_frequency)

    schedule = []
    balance = principal
    cumulative_interest = 0.0
    cumulative_principal = 0.0

    start = datetime.fromisoformat(start_date) if start_date else datetime.utcnow()

    for i in range(1, number_of_payments + 1):
        # Calculate interest for this period
        interest_payment = balance * periodic_rate
        
        # Calculate principal payment
        principal_payment = payment - interest_payment
        
        # Handle final payment (may be slightly different due to rounding)
        if i == number_of_payments:
            principal_payment = balance
        
        # Ensure we don't pay more than remaining balance
        if principal_payment > balance:
            principal_payment = balance

        actual_payment = principal_payment + interest_payment
        
        # Update balance
        balance = max(0, balance - principal_payment)
        
        # Update cumulative totals
        cumulative_interest += interest_payment
        cumulative_principal += principal_payment

        # Calculate due date based on frequency
        due_date = calculate_due_date(start, i, payment_frequency)

        schedule.append(PaymentSchedule(
            payment_number=i,
            due_date=due_date.strftime("%Y-%m-%d"),
            payment=actual_payment,
            principal=principal_payment,
            interest=interest_payment,
            balance=balance,
            cumulative_interest=cumulative_interest,
            cumulative_principal=cumulative_principal
        ))

    return LoanSummary(
        principal=principal,
        total_interest=cumulative_interest,
        total_payment=principal + cumulative_interest,
        monthly_payment=payment,
        number_of_payments=number_of_payments,
        effective_rate=annual_interest_rate,
        schedule=schedule
    )


def calculate_due_date(
    start_date: datetime,
    payment_number: int,
    frequency: str
) -> datetime:
    """Calculate due date for a payment based on frequency"""
    if frequency == "monthly":
        return start_date + relativedelta(months=payment_number)
    elif frequency == "weekly":
        return start_date + timedelta(weeks=payment_number)
    elif frequency == "biweekly":
        return start_date + timedelta(weeks=payment_number * 2)
    else:
        return start_date + relativedelta(months=payment_number)


def calculate_simple_interest(
    principal: float,
    annual_interest_rate: float,
    term_in_days: int
) -> float:
    """
    Calculate simple interest (for comparison or legacy loans)
    
    Args:
        principal: Loan principal amount
        annual_interest_rate: Annual interest rate as percentage
        term_in_days: Loan term in days
    
    Returns:
        Simple interest amount
    """
    rate = annual_interest_rate / 100
    interest = principal * rate * (term_in_days / 365)
    return round(interest, 2)


def calculate_reducing_balance_interest(
    principal: float,
    annual_interest_rate: float,
    term_in_months: int
) -> float:
    """
    Calculate reducing balance interest
    
    Args:
        principal: Loan principal amount
        annual_interest_rate: Annual interest rate as percentage
        term_in_months: Loan term in months
    
    Returns:
        Total interest amount using reducing balance method
    """
    schedule = generate_amortization_schedule(principal, annual_interest_rate, term_in_months)
    return schedule.total_interest


def calculate_affordability(
    monthly_income: float,
    existing_monthly_debts: float,
    proposed_loan_payment: float
) -> Dict:
    """
    Calculate loan affordability based on income
    
    Args:
        monthly_income: Monthly gross income
        existing_monthly_debts: Current monthly debt obligations
        proposed_loan_payment: Proposed monthly loan payment
    
    Returns:
        Dictionary with affordability metrics
    """
    current_dti = (existing_monthly_debts / monthly_income) * 100 if monthly_income > 0 else 0
    new_dti = ((existing_monthly_debts + proposed_loan_payment) / monthly_income) * 100 if monthly_income > 0 else 0
    is_affordable = new_dti <= 36  # 36% DTI is recommended max

    # Calculate max affordable payment (36% of income - existing debts)
    max_affordable_payment = max(0, monthly_income * 0.36 - existing_monthly_debts)

    return {
        "current_dti": round(current_dti, 1),
        "new_dti": round(new_dti, 1),
        "is_affordable": is_affordable,
        "max_affordable_payment": round(max_affordable_payment, 2),
    }


def calculate_max_loan_amount(
    monthly_payment_capacity: float,
    annual_interest_rate: float,
    term_in_months: int
) -> float:
    """
    Calculate maximum loan amount based on monthly payment capacity
    
    Args:
        monthly_payment_capacity: Maximum affordable monthly payment
        annual_interest_rate: Annual interest rate as percentage
        term_in_months: Loan term in months
    
    Returns:
        Maximum loan amount
    """
    if monthly_payment_capacity <= 0 or term_in_months <= 0:
        return 0.0

    if annual_interest_rate == 0:
        return monthly_payment_capacity * term_in_months

    monthly_rate = annual_interest_rate / 100 / 12

    # Rearrange the monthly payment formula to solve for P (principal)
    # P = M × [(1+r)^n - 1] / [r(1+r)^n]
    max_loan = (
        monthly_payment_capacity * (math.pow(1 + monthly_rate, term_in_months) - 1)
    ) / (monthly_rate * math.pow(1 + monthly_rate, term_in_months))

    return round(max_loan, 2)


def calculate_payment_breakdown(
    remaining_balance: float,
    annual_interest_rate: float,
    regular_payment: float
) -> Dict:
    """
    Calculate payment breakdown at any point in the loan
    
    Args:
        remaining_balance: Current outstanding balance
        annual_interest_rate: Annual interest rate as percentage
        regular_payment: Regular payment amount
    
    Returns:
        Dictionary with interest and principal breakdown
    """
    monthly_rate = annual_interest_rate / 100 / 12
    interest = remaining_balance * monthly_rate
    principal = min(regular_payment - interest, remaining_balance)

    return {
        "interest": round(interest, 2),
        "principal": round(principal, 2),
        "total": round(interest + principal, 2)
    }


def validate_loan_parameters(
    principal: float,
    annual_interest_rate: float,
    term_in_months: int
) -> Dict:
    """
    Validate loan parameters
    
    Returns:
        Dictionary with validation result and error messages
    """
    errors = []

    if principal <= 0:
        errors.append("Principal amount must be greater than zero")

    if principal > 10000000:
        errors.append("Principal amount seems unreasonably high")

    if annual_interest_rate < 0 or annual_interest_rate > 100:
        errors.append("Interest rate must be between 0% and 100%")

    if term_in_months <= 0:
        errors.append("Loan term must be greater than zero")

    if term_in_months > 360:
        errors.append("Loan term cannot exceed 30 years (360 months)")

    return {
        "is_valid": len(errors) == 0,
        "errors": errors
    }


def calculate_early_payoff_savings(
    current_balance: float,
    remaining_payments: int,
    regular_monthly_payment: float,
    annual_interest_rate: float,
    extra_payment: float
) -> Dict:
    """
    Calculate savings from making extra payments
    
    Returns:
        Dictionary with months saved, interest saved, and new payoff date
    """
    # Generate original schedule
    original_schedule = generate_amortization_schedule(
        current_balance,
        annual_interest_rate,
        remaining_payments
    )

    # Simulate extra payments
    monthly_rate = annual_interest_rate / 100 / 12
    balance = current_balance
    months = 0
    total_interest = 0.0
    payment_with_extra = regular_monthly_payment + extra_payment

    while balance > 0 and months < remaining_payments:
        months += 1
        interest = balance * monthly_rate
        principal = min(payment_with_extra - interest, balance)
        
        total_interest += interest
        balance -= principal

        if balance < 0.01:  # Account for rounding
            break

    months_saved = remaining_payments - months
    interest_saved = original_schedule.total_interest - total_interest
    
    new_payoff_date = datetime.utcnow() + relativedelta(months=months)

    return {
        "months_saved": months_saved,
        "interest_saved": round(interest_saved, 2),
        "new_payoff_date": new_payoff_date.strftime("%Y-%m-%d")
    }
