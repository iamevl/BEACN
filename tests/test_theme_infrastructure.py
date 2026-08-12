import subprocess
from pathlib import Path

from jinja2 import ChainableUndefined, Environment, FileSystemLoader


ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "console" / "templates"
COMPLETE_TEMPLATES = (
    "index.html",
    "settings.html",
    "login.html",
    "setup.html",
    "forgot-password.html",
    "reset-password.html",
)


def test_complete_templates_bootstrap_before_stylesheets_and_load_controller():
    include = "{% include '_theme_bootstrap.html' %}"
    controller = "filename='js/theme.js'"

    for filename in COMPLETE_TEMPLATES:
        source = (TEMPLATES / filename).read_text()

        assert source.count(include) == 1, filename
        assert source.index(include) < source.index('rel="stylesheet"'), filename
        assert source.count(controller) == 1, filename
        assert source.index(controller) < source.index("</body>"), filename


def test_rendered_templates_bootstrap_before_stylesheets():
    environment = Environment(
        loader=FileSystemLoader(TEMPLATES),
        undefined=ChainableUndefined,
        autoescape=True,
    )
    environment.globals["url_for"] = (
        lambda _endpoint, **values: f"/static/{values.get('filename', '')}"
    )

    for filename in COMPLETE_TEMPLATES:
        rendered = environment.get_template(filename).render()

        assert rendered.count("const storageKey = 'beacn.appearance';") == 1
        assert rendered.index("const storageKey") < rendered.index(
            'rel="stylesheet"'
        )
        assert rendered.count("/static/js/theme.js") == 1


def test_settings_exposes_exact_appearance_contract_without_server_form():
    source = (TEMPLATES / "settings.html").read_text()
    theme_row = source[source.index("<strong>Theme</strong>") :]
    theme_row = theme_row[: theme_row.index("</div>\n        </article>")]

    assert '<select\n                id="appearance"' in theme_row
    assert theme_row.count("<option") == 3
    assert '<option value="dark">Dark</option>' in theme_row
    assert '<option value="light">Light</option>' in theme_row
    assert '<option value="system">System</option>' in theme_row
    assert "<form" not in theme_row


def test_dark_and_light_palettes_define_critical_semantic_tokens():
    source = (
        ROOT / "console" / "static" / "branding" / "css" / "beacn-tokens.css"
    ).read_text()
    critical = {
        "--beacn-page",
        "--beacn-page-highlight",
        "--beacn-elevated",
        "--beacn-panel",
        "--beacn-surface-subtle",
        "--beacn-primary-text",
        "--beacn-secondary-text",
        "--beacn-border",
        "--beacn-border-strong",
        "--beacn-accent",
        "--beacn-input",
        "--beacn-table-header",
        "--beacn-modal",
        "--beacn-code",
        "--beacn-shadow",
        "--beacn-overlay",
        "--beacn-focus-ring",
    }
    dark = source[source.index(':root[data-theme="dark"]') :]
    dark = dark[: dark.index(':root[data-theme="light"]')]
    light = source[source.index(':root[data-theme="light"]') :]

    for token in critical:
        assert f"{token}:" in dark
        assert f"{token}:" in light


def test_canvas_modules_use_theme_event_and_semantic_colours():
    for filename in ("charts.js", "device-types.js"):
        source = (ROOT / "console" / "static" / "js" / filename).read_text()
        assert "beacn:themechange" in source
        assert "getComputedStyle(document.documentElement)" in source


def test_auth_styles_and_wordmark_support_both_effective_themes():
    auth_css = (ROOT / "console" / "static" / "css" / "auth.css").read_text()
    index = (TEMPLATES / "index.html").read_text()
    light_logo = (
        ROOT
        / "console"
        / "static"
        / "branding"
        / "logos"
        / "beacn-secondary-logo-light.svg"
    )

    assert ':root[data-theme="dark"]' in auth_css
    assert ':root[data-theme="light"]' in auth_css
    assert "beacn-secondary-logo-dark.svg" in index
    assert "beacn-secondary-logo-light.svg" in index
    assert light_logo.is_file()


def test_theme_javascript_contract():
    result = subprocess.run(
        ["node", "--test", "tests/theme_runtime_test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
