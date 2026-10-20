"""The scam and phishing check (924): heuristics on pasted text; it never opens a link."""
import pytest

from minty_core.scamcheck import check

SCAMS = [
    "Dear customer, your HDFC account will be blocked today. Update KYC immediately: http://hdfc-kyc-update.in/login",
    "Congratulations! You have won Rs 25,00,000 in the KBC lottery. To claim, send Rs 5000 processing fee to 9876500011",
    "Your SBI YONO account is suspended. Share your OTP to reactivate within 2 hours.",
    "Hi, I'm from Amazon support. Please install AnyDesk so I can process your refund.",
    "Part time job! Earn Rs 5000 daily by liking YouTube videos. Message on WhatsApp now: wa.me/919876543210",
    "You have received a collect request of Rs 4999 from Google Pay. Enter your UPI PIN to receive the money.",
    "Your electricity will be disconnected tonight at 9:30pm because previous month bill is not updated. Call 9812345678 immediately",
    "Income tax refund of Rs 15,640 is pending. Click here to claim: bit.ly/3xYzRefund",
    "Your parcel is held at customs. Pay Rs 49 clearance fee at http://india-post-tracking.top to release it.",
    "Dear user your Paytm KYC has expired, account will be closed. Send your Aadhaar and PAN photo to this number to verify.",
    "Hello sir this is your bank manager, we detected fraud on your card, please tell me the CVV and expiry to block it.",
    "Urgent!! Your SIM will be deactivated in 24 hours. Press 1 to talk to executive or reply YES to update KYC.",
    "Dear customer your debit card is blocked. To unblock click: hdfcbank-secure-login.com and enter card number, PIN",
    "I accidentally sent Rs 10,000 to your number via PhonePe, please send it back to this UPI id: ramesh@ybl",
    "You've been selected for a free iPhone 15! Pay Rs 199 shipping only. Hurry, offer ends today: http://free-iphone-india.xyz",
    "Your Netflix payment failed. Update billing details within 12 hours or account will be terminated: netflix-billing.support",
    "Work from home, no experience needed, earn Rs 30000 per month, registration fee Rs 500 only. Telegram: @easyjobs",
    "Police notice: a case has been filed against you. To avoid arrest, pay the fine immediately via this link.",
    "Your OTP for transaction is 884211. If you did not request this, share it with our agent at 9988776655 to cancel.",
    "Dear customer, a gift voucher of Rs 5000 is waiting. Install this APK to claim: http://gift-claim-india.com/app.apk",
]
GENUINE = [
    "Rs.1,234.50 debited from A/c XX1234 on 12-10-26 UPI Ref 412345678901. Not you? Call 1800 1080.",
    "INR 5,000.00 credited to your a/c XX1234 on 13-10-26 by NEFT from RAVI KUMAR.",
    "Your OTP is 482913 for login to Swiggy. Do not share it with anyone.",
    "482913 is your OTP for HDFC Bank NetBanking. Never share OTP with anyone, including bank staff.",
    "Your Uber ride with Amit is arriving. Share OTP 4821 with your driver.",
    "Your Zomato order #48211 has been delivered. Rate your experience.",
    "Amazon: Your package will arrive today by 8 PM. Track: amazon.in/your-orders",
    "Dear customer, your electricity bill of Rs 912 for September is due on 24-10-2026. Pay at bescom.in or the app.",
    "Your Airtel recharge of Rs 299 is successful. Validity 28 days. Thank you for choosing Airtel.",
    "Reminder: your HDFC credit card payment of Rs 4,520 is due on 25 Oct. Pay via NetBanking or the app.",
    "Your appointment at Apollo Clinic is confirmed for 21 Oct at 5:30 PM.",
    "Hi, class 10 maths test moved to Thursday. - Mrs Sharma",
    "Your Spotify Premium renews on 5 Nov for Rs 119. Manage at spotify.com/account.",
    "Cult.fit: Your session at 6:30 AM tomorrow is booked. See you there!",
    "Rs 250.00 paid to ZOMATO via UPI from A/c XX1234 on 14-10-26. Ref 556677889900.",
    "IRCTC: Your ticket PNR 4512398765 is confirmed. Coach S4 Seat 32. Happy journey.",
    "Your Jio number's data pack is 90% used. Recharge on jio.com or the MyJio app.",
    "Bank alert: Your FD of Rs 50,000 matures on 30-10-26. Visit the branch or the app to renew.",
    "Your OTP 735201 is for Flipkart payment of Rs 1,299. Valid for 10 minutes. Do not share.",
    "Dad: reached home, send me the notes photo please",
]


@pytest.mark.parametrize("text", SCAMS)
def test_each_fixture_scam_is_flagged(text):
    r = check(text)
    assert r.verdict in ("likely a scam", "suspicious"), (r.score, r.reasons, text)
    assert r.reasons


def test_at_most_two_of_twenty_genuine_messages_are_flagged():
    flagged = [t for t in GENUINE if check(t).verdict != "no red flags found"]
    assert len(flagged) <= 2, flagged


def test_the_obvious_ones_are_called_likely_not_just_suspicious():
    assert check(SCAMS[2]).verdict == "likely a scam"
    assert check(SCAMS[3]).verdict == "likely a scam"
    assert check(SCAMS[19]).verdict == "likely a scam"


def test_the_reply_never_calls_it_safe_and_reminds_that_nobody_genuine_asks_for_an_otp():
    clean = check("Dad: reached home, send me the notes photo please")
    assert clean.verdict == "no red flags found" and "not a guarantee" in clean.text.lower()
    scam = check(SCAMS[2])
    assert "never" in scam.text.lower() and "otp" in scam.text.lower()
    assert "safe" not in clean.text.lower().replace("not a guarantee", "")


def test_a_link_is_named_but_never_followed():
    r = check("Click http://hdfc-kyc-update.in/login now")
    assert "hdfc-kyc-update.in" in r.text and r.links == ["hdfc-kyc-update.in"]
