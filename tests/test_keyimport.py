import pytest

from minty_core.keyimport import extract_keys

# Synthetic, obviously fake values: none of these is a real credential.
GROQ = "gsk_" + "AbCdEfGhIjKlMnOpQrStUvWxYz0123456789abcd"
NVIDIA = "nvapi-" + "FAKEfakeFAKEfake_0123456789-abcdefghijklmn"
OPENROUTER = "sk-or-v1-" + "0123456789abcdef0123456789abcdef"
ANTHROPIC = "sk-ant-" + "api03-FAKEFAKEFAKE_fake-0123456789abcd"
GEMINI = "AIza" + "SyFAKEfakeFAKEfake_0123456789-abcdefgh"
CEREBRAS = "csk-" + "fakefakefakefake0123456789"
MISTRAL = "mistralFAKE0123456789abcdefghijklmnop"
BRAVE = "BSAfakefakefake0123456789ABCDEF"


def test_labelled_lines_with_optional_separator_and_case():
    text = (
        f"groq {GROQ}\n"
        f"Mistral: {MISTRAL}\n"
        f"  BRAVE = {BRAVE}\n"
        f"Cerebras:{CEREBRAS}\n"
        f"anthropic {ANTHROPIC}\n"
    )
    assert extract_keys(text) == {
        "groq": GROQ,
        "mistral": MISTRAL,
        "plugin.web_search.brave_key": BRAVE,
        "cerebras": CEREBRAS,
        "anthropic": ANTHROPIC,
    }


def test_open_router_label_with_and_without_space():
    assert extract_keys(f"open router {OPENROUTER}") == {"openrouter": OPENROUTER}
    assert extract_keys(f"OpenRouter: {OPENROUTER}") == {"openrouter": OPENROUTER}


def test_google_ai_studio_and_gemini_labels_map_to_gemini():
    assert extract_keys(f"google ai studio {GEMINI}") == {"gemini": GEMINI}
    assert extract_keys(f"Gemini = {GEMINI}") == {"gemini": GEMINI}


def test_nvapi_key_found_inside_code_sample():
    text = (
        "import requests\n"
        "headers = {\n"
        f'    "Authorization": "Bearer {NVIDIA}",\n'
        '    "Accept": "application/json",\n'
        "}\n"
    )
    assert extract_keys(text) == {"nvidia": NVIDIA}


def test_unlabelled_patterns_for_every_provider():
    text = "\n".join([
        f"x = '{GROQ}'",
        f"y = '{OPENROUTER}'",
        f"z = '{ANTHROPIC}'",
        f"g = '{GEMINI}'",
        f"c = '{CEREBRAS}'",
        f"n = '{NVIDIA}'",
    ])
    assert extract_keys(text) == {
        "groq": GROQ, "openrouter": OPENROUTER, "anthropic": ANTHROPIC,
        "gemini": GEMINI, "cerebras": CEREBRAS, "nvidia": NVIDIA,
    }


def test_labelled_beats_pattern_regardless_of_order():
    pattern_only = "gsk_" + "Z" * 30
    labelled = "gsk_" + "Q" * 30
    before = f"key = {pattern_only}\ngroq: {labelled}\n"
    after = f"groq: {labelled}\nkey = {pattern_only}\n"
    assert extract_keys(before) == {"groq": labelled}
    assert extract_keys(after) == {"groq": labelled}


def test_labelled_value_need_not_match_a_pattern():
    # Mistral and Brave keys have no recognisable prefix: only the label identifies them.
    assert extract_keys(f"mistral {MISTRAL}") == {"mistral": MISTRAL}


def test_duplicates_keep_first_labelled_else_first_pattern_match():
    first = "gsk_" + "A" * 30
    second = "gsk_" + "B" * 30
    third = "gsk_" + "C" * 30
    assert extract_keys(f"a = {first}\nb = {second}\n") == {"groq": first}
    assert extract_keys(f"a = {first}\ngroq {second}\ngroq {third}\n") == {"groq": second}
    assert extract_keys(f"gemini {GEMINI}\ngoogle ai studio {GEMINI[:-1]}X\n") == {"gemini": GEMINI}


def test_short_tokens_ignored():
    text = "groq: short\nmistral abc\ngsk_tooShort\nnvapi-tiny\nAIzaShort\ncsk-nope\nsk-ant-x\nsk-or-v1-x\n"
    assert extract_keys(text) == {}


def test_surrounding_quotes_and_bearer_are_stripped():
    assert extract_keys(f'groq: "{GROQ}"') == {"groq": GROQ}
    assert extract_keys(f"mistral = '{MISTRAL}',") == {"mistral": MISTRAL}
    assert extract_keys(f"nvidia: Bearer {NVIDIA}") == {"nvidia": NVIDIA}
    assert extract_keys(f'nvidia = "Bearer {NVIDIA}"') == {"nvidia": NVIDIA}


def test_only_documented_provider_names_are_imported():
    text = (
        f"openai {MISTRAL}\n"
        f"github {MISTRAL}\n"
        f"token: {MISTRAL}\n"
        "sk-proj-" + "a" * 40 + "\n"
    )
    assert extract_keys(text) == {}


def test_label_must_be_separated_from_value():
    # "nvidia_api_key=..." in a code sample is not a "nvidia <value>" line.
    text = f'nvidia_api_key = "{NVIDIA}"\ngroq_key={GROQ}\n'
    assert extract_keys(text) == {"nvidia": NVIDIA, "groq": GROQ}


def test_url_after_label_is_not_a_key():
    assert extract_keys("brave: https://api.search.brave.com/res/v1/web/search") == {}


def test_prefix_inside_a_longer_token_is_not_a_key():
    long_token = "sk-ant-api03-" + "x" * 10 + "csk-" + "y" * 30
    assert extract_keys(long_token) == {"anthropic": long_token}


def test_empty_and_noise_text():
    assert extract_keys("") == {}
    assert extract_keys("# my notes\nhello world\n\n   \n") == {}


def test_windows_line_endings():
    assert extract_keys(f"groq {GROQ}\r\nmistral: {MISTRAL}\r\n") == {"groq": GROQ, "mistral": MISTRAL}


@pytest.mark.parametrize("line", [
    "groq llama-3.3-70b-versatile",
    "groq: <your-key-goes-here>",
    "gemini gemini-2.0-flash-exp",
    "nvidia meta/llama-3.1-70b-instruct",
    "openrouter: openrouter/auto-router-model",
    "anthropic: claude-3-5-sonnet-latest",
    "cerebras: llama3.1-8b-instruct-model",
    "brave: not_configured_yet",
    "mistral: YOUR_API_KEY_HERE",
])
def test_garbage_labelled_values_are_not_stored(line):
    assert extract_keys(line) == {}


def test_real_prefix_key_wins_over_garbage_labelled_line():
    text = f"groq llama-3.3-70b-versatile\nsome code: x = '{GROQ}'\nnvidia meta/llama-3.1-70b-instruct\nnvidia {NVIDIA}\n"
    assert extract_keys(text) == {"groq": GROQ, "nvidia": NVIDIA}


def test_prefix_providers_accept_labelled_values_only_in_their_own_shape():
    # Both directions: an anthropic key labelled openrouter and vice versa.
    assert extract_keys(f"openrouter: {ANTHROPIC}") == {"anthropic": ANTHROPIC}
    assert extract_keys(f"anthropic: {OPENROUTER}") == {"openrouter": OPENROUTER}
    assert extract_keys(f"groq {NVIDIA}\nnvidia {GROQ}") == {"nvidia": NVIDIA, "groq": GROQ}


def test_mistral_and_brave_reject_values_shaped_like_other_providers_keys():
    assert extract_keys(f"mistral: {GROQ}") == {"groq": GROQ}
    assert extract_keys(f"brave {OPENROUTER}") == {"openrouter": OPENROUTER}


@pytest.mark.parametrize("value", [
    "path/with/slashes12345", "<angle-bracket-12>", "my_EXAMPLE_value_123", "xxxxxxxxxxxxxxxx",
    "ChangeMe123456789", "TODO_fill_this_in", "not_set_up_yet_123", "mistral-large-latest",
    "codestral-versatile", "open-mixtral-8x7b-instruct", "mixtral-8x7b-32768", "llama-3.1-70b-chat",
    "put_your_key_here",
])
@pytest.mark.parametrize("label", ["mistral", "brave"])
def test_arbitrary_token_providers_reject_placeholders_and_model_names(label, value):
    assert extract_keys(f"{label}: {value}") == {}


def test_arbitrary_token_providers_still_accept_real_looking_tokens():
    assert extract_keys(f"mistral {MISTRAL}\nbrave {BRAVE}") == {"mistral": MISTRAL, "plugin.web_search.brave_key": BRAVE}
