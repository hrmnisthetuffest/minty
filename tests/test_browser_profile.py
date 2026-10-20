

def test_the_agent_is_told_to_sign_up_with_the_saved_email_and_to_leave_the_password_to_the_user():
    from minty_core.plugins.builtin.browser.plugin import AGENT_PROMPT

    text = " ".join(AGENT_PROMPT.split())
    assert "sign up or register" in text and "saved e-mail address" in text
    assert "never choose or type a password" in text and "CAPTCHA" in text and "confirmation link" in text
