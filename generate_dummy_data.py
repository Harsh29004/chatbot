"""
Generate dummy FAQ Excel files for development and testing.

Run:  python generate_dummy_data.py
"""

import pandas as pd

CUSTOMER_ROWS = [
    {
        "Question": "How do I cancel a booking?",
        "Alt_Phrasings": "cancel order; cancel my service; stop booking",
        "Category": "Bookings",
        "Answer": (
            "To cancel a booking, go to My Bookings, tap the booking you want "
            "to cancel, and press \"Cancel Booking.\" Cancellations made more "
            "than 2 hours before the scheduled time are free; cancellations "
            "within 2 hours incur a ₹50 fee."
        ),
    },
    {
        "Question": "How do I get a refund?",
        "Alt_Phrasings": "refund status; where is my refund; money back",
        "Category": "Payments",
        "Answer": (
            "Refunds are processed within 5-7 business days to your original "
            "payment method. To check refund status, go to My Bookings → "
            "select the cancelled booking → tap \"Refund Status.\""
        ),
    },
    {
        "Question": "How do I change my phone number?",
        "Alt_Phrasings": "update mobile number; change contact number",
        "Category": "Account",
        "Answer": (
            "Go to Profile → Edit Profile → tap your phone number → enter "
            "the new number and verify with OTP. Your booking history and "
            "wallet balance will remain unchanged."
        ),
    },
    {
        "Question": "What payment methods do you accept?",
        "Alt_Phrasings": "payment options; how to pay; UPI; card",
        "Category": "Payments",
        "Answer": (
            "We accept UPI, credit/debit cards, net banking, and Instant "
            "Sahay wallet balance. Cash payment is available for select "
            "services and will be shown at checkout if applicable."
        ),
    },
    {
        "Question": "How do I reschedule a booking?",
        "Alt_Phrasings": "change booking time; move appointment; shift booking",
        "Category": "Bookings",
        "Answer": (
            "Open My Bookings, select the booking, tap \"Reschedule,\" and "
            "choose a new date and time. Rescheduling is free if done more "
            "than 4 hours before the original slot."
        ),
    },
]

PARTNER_ROWS = [
    {
        "Question": "How do I check my KYC status?",
        "Alt_Phrasings": "KYC verification; document status; identity check",
        "Category": "KYC",
        "Answer": (
            "Go to your Partner Dashboard → Profile → KYC Status. You'll see "
            "each document listed with its verification status (Pending, "
            "Verified, or Rejected). If a document is rejected, tap it to "
            "see the reason and re-upload."
        ),
    },
    {
        "Question": "When do I receive my payout?",
        "Alt_Phrasings": "payment cycle; when do I get paid; payout schedule",
        "Category": "Payouts",
        "Answer": (
            "Payouts are processed every Monday and Thursday for completed "
            "jobs. The amount is transferred to your registered bank account "
            "within 1-2 business days after processing. Ensure your bank "
            "details are up to date in Profile → Bank Details."
        ),
    },
    {
        "Question": "How are jobs assigned to me?",
        "Alt_Phrasings": "job allocation; how do I get orders; assignment system",
        "Category": "Jobs",
        "Answer": (
            "Jobs are assigned based on your proximity to the customer, your "
            "service category, your rating, and your availability status. "
            "Keep your availability toggle ON in the app to receive job "
            "requests. Higher-rated partners receive priority."
        ),
    },
    {
        "Question": "How is my rating calculated?",
        "Alt_Phrasings": "star rating; customer reviews; rating system",
        "Category": "Ratings",
        "Answer": (
            "Your rating is a rolling average of your last 50 completed jobs. "
            "Customers rate you on punctuality, quality, and behaviour. "
            "Ratings below 3.5 may result in reduced job assignments. You can "
            "view detailed feedback in Dashboard → Ratings."
        ),
    },
    {
        "Question": "What happens if I cancel a job after accepting?",
        "Alt_Phrasings": "job cancellation penalty; cancel accepted job",
        "Category": "Penalties",
        "Answer": (
            "Cancelling an accepted job incurs a penalty of ₹100 and "
            "negatively impacts your reliability score. Three cancellations "
            "within 7 days may result in a temporary 24-hour suspension. "
            "If you have a genuine emergency, contact partner support before "
            "cancelling to request a waiver."
        ),
    },
]


def main():
    customer_df = pd.DataFrame(CUSTOMER_ROWS)
    customer_df.to_excel(
        "apps/customer_bot/data/customer_faq.xlsx",
        index=False,
        engine="openpyxl",
    )
    print("Created apps/customer_bot/data/customer_faq.xlsx")

    partner_df = pd.DataFrame(PARTNER_ROWS)
    partner_df.to_excel(
        "apps/partner_bot/data/partner_faq.xlsx",
        index=False,
        engine="openpyxl",
    )
    print("Created apps/partner_bot/data/partner_faq.xlsx")


if __name__ == "__main__":
    main()
