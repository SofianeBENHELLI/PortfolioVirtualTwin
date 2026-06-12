"""Editable MD prompt loading: file override, placeholder validation, safe render."""
from app.agents import prompts as P


def test_defaults_used_when_file_missing(monkeypatch, tmp_path):
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)  # empty dir
    for name in ("Bull", "Bear", "Judge"):
        assert P.load_prompt(name) == P.DEFAULTS[name]


def test_file_overrides_default(monkeypatch, tmp_path):
    (tmp_path / "Bear.md").write_text("Custom bear for {sym}. Data: {data}", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    assert P.load_prompt("Bear").startswith("Custom bear")
    assert P.load_prompt("Bull") == P.DEFAULTS["Bull"]  # untouched


def test_file_missing_placeholder_rejected(monkeypatch, tmp_path):
    (tmp_path / "Bull.md").write_text("No placeholders here at all", encoding="utf-8")
    monkeypatch.setattr(P, "PROMPTS_DIR", tmp_path)
    assert P.load_prompt("Bull") == P.DEFAULTS["Bull"]


def test_repo_prompt_files_are_valid():
    """The committed backend/prompts/*.md files must contain their required placeholders."""
    for name in ("Bull", "Bear", "Judge"):
        text = P.load_prompt(name)
        assert text != P.DEFAULTS[name] or all(p in text for p in P.REQUIRED_PLACEHOLDERS[name]), name
        for ph in P.REQUIRED_PLACEHOLDERS[name]:
            assert ph in text, f"{name}.md missing {ph}"


def test_render_fills_and_tolerates_unknown_tokens():
    out = P.render("Judge {sym}: bull={bull_case} bear={bear_case} keep {unknown}",
                   sym="AAPL", bull_case="up", bear_case="down")
    assert out == "Judge AAPL: bull=up bear=down keep {unknown}"


def test_render_judge_default_full():
    out = P.render(P.DEFAULTS["Judge"], sym="AAPL", data={"price": 1}, style="growth",
                   horizon="6m", bull_strength="70", bull_case="b", bull_points="p1",
                   bear_strength="40", bear_case="c", bear_points="p2")
    assert "{" not in out.replace("{'price': 1}", "")  # everything substituted
