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


def test_settings_theme_control_remains_dark_and_static():
    source = (TEMPLATES / "settings.html").read_text()
    theme_row = source[source.index("<strong>Theme</strong>") :]
    theme_row = theme_row[: theme_row.index("</div>\n        </article>")]

    assert '<span class="badge">Dark</span>' in theme_row
    assert "<select" not in theme_row
    assert 'value="light"' not in source
    assert 'value="system"' not in source


def test_theme_javascript_contract():
    result = subprocess.run(
        ["node", "--test", "tests/theme_runtime_test.js"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
