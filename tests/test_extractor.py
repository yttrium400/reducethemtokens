import textwrap
import tempfile
import os
from pathlib import Path

from rtt.extractor import _extract_file
from rtt.formatter import format_file_text, format_file_markdown


def write_temp(content: str, suffix: str) -> str:
    f = tempfile.NamedTemporaryFile(mode="w", suffix=suffix, delete=False)
    f.write(content)
    f.flush()
    return f.name


def test_python_function():
    code = textwrap.dedent("""
        def greet(name: str) -> str:
            \"\"\"Say hello.\"\"\"
            return f"Hello {name}"

        class Greeter:
            def hello(self, name: str) -> None:
                pass
    """)
    path = write_temp(code, ".py")
    try:
        fi = _extract_file(path)
        assert fi is not None
        assert fi.language == "python"
        names = [s.name for s in fi.symbols]
        assert "greet" in names
        assert "Greeter" in names
        greet = next(s for s in fi.symbols if s.name == "greet")
        assert greet.docstring == "Say hello."
    finally:
        os.unlink(path)


def test_format_text():
    from rtt import FileIndex, Symbol
    fi = FileIndex(
        path="src/foo.py",
        language="python",
        imports=["os", "sys"],
        symbols=[
            Symbol(name="bar", kind="function", signature="def bar(x: int) -> str", docstring="Does bar.")
        ]
    )
    text = format_file_text(fi)
    assert "src/foo.py" in text
    assert "def bar(x: int) -> str" in text
    assert "Does bar." in text


def test_format_markdown():
    from rtt import FileIndex, Symbol
    fi = FileIndex(
        path="src/foo.py",
        language="python",
        imports=["os"],
        symbols=[Symbol(name="bar", kind="function", signature="def bar(x: int) -> str")]
    )
    md = format_file_markdown(fi)
    assert "## `src/foo.py`" in md
    assert "bar" in md


def test_token_count():
    from rtt.tokenizer import count_tokens
    n = count_tokens("hello world")
    assert n > 0


def test_swift_extraction_fixture():
    path = Path(__file__).parent / "fixtures" / "sample.swift"
    fi = _extract_file(str(path))
    assert fi is not None
    assert fi.language == "swift"
    assert "Foundation" in fi.imports

    names = [s.name for s in fi.symbols]
    assert "Greeter" in names
    assert "Person" in names
    assert "Service" in names
    assert "Status" in names

    greeter = next(s for s in fi.symbols if s.name == "Greeter")
    assert greeter.kind == "protocol"
    assert [child.name for child in greeter.children] == ["greet"]
    assert greeter.children[0].signature == "func greet(name: String) -> String"

    person = next(s for s in fi.symbols if s.name == "Person" and s.kind == "struct")
    assert [child.name for child in person.children] == ["greet"]
    assert person.children[0].signature == "func greet(name: String) -> String"

    service = next(s for s in fi.symbols if s.name == "Service")
    assert service.children[0].signature == "func run(count: Int) async throws -> Void"


def test_kotlin_extraction_fixture():
    path = Path(__file__).parent / "fixtures" / "sample.kt"
    fi = _extract_file(str(path))
    assert fi is not None
    assert fi.language == "kotlin"
    assert "kotlin.collections.List" in fi.imports
    assert "java.time.Instant" in fi.imports

    names = [s.name for s in fi.symbols]
    assert "Greeter" in names
    assert "User" in names
    assert "Result" in names
    assert "Registry" in names
    assert "topLevel" in names

    greeter = next(s for s in fi.symbols if s.name == "Greeter")
    assert greeter.kind == "interface"
    assert greeter.children[0].signature == "fun greet(name: String): String"

    user = next(s for s in fi.symbols if s.name == "User")
    assert user.kind == "class"
    assert user.children[0].signature == "override fun greet(name: String): String"

    registry = next(s for s in fi.symbols if s.name == "Registry")
    assert registry.kind == "object"
    assert registry.children[0].signature == "fun lookup(id: String): User?"


def test_lua_extraction_fixture():
    path = Path(__file__).parent / "fixtures" / "sample.lua"
    fi = _extract_file(str(path))
    assert fi is not None
    assert fi.language == "lua"
    assert "base" in fi.imports
    assert "utils" in fi.imports

    names = [s.name for s in fi.symbols]
    assert "M" in names
    assert "M.greet" in names
    assert "M:add" in names
    assert "helper" in names

    greet = next(s for s in fi.symbols if s.name == "M.greet")
    assert greet.kind == "function"
    assert greet.signature == "function M.greet(name)"

    add = next(s for s in fi.symbols if s.name == "M:add")
    assert add.kind == "function"
    assert add.signature == "function M:add(a, b)"

    helper = next(s for s in fi.symbols if s.name == "helper")
    assert helper.kind == "function"
    assert helper.signature == "local function helper()"
    path = Path(__file__).parent / "fixtures" / "sample.cs"
    fi = _extract_file(str(path))
    assert fi is not None
    assert fi.language == "csharp"
    assert "System" in fi.imports
    assert "System.Collections.Generic" in fi.imports

    names = [s.name for s in fi.symbols]
    assert "IGreeter" in names
    assert "Greeter" in names
    assert "Point" in names
    assert "Color" in names
    assert "MathHelper" in names

    igreeter = next(s for s in fi.symbols if s.name == "IGreeter")
    assert igreeter.kind == "interface"

    greeter = next(s for s in fi.symbols if s.name == "Greeter")
    assert greeter.kind == "class"
    method_names = [c.name for c in greeter.children]
    assert "Greet" in method_names

    point = next(s for s in fi.symbols if s.name == "Point")
    assert point.kind == "struct"

    color = next(s for s in fi.symbols if s.name == "Color")
    assert color.kind == "enum"


def test_php_extraction_fixture():
    path = Path(__file__).parent / "fixtures" / "sample.php"
    fi = _extract_file(str(path))
    assert fi is not None
    assert fi.language == "php"
    assert any("Controller" in i for i in fi.imports)

    names = [s.name for s in fi.symbols]
    assert "BaseUser" in names
    assert "createUser" in names
    assert "Status" in names

    base_user = next(s for s in fi.symbols if s.name == "BaseUser")
    assert base_user.kind == "class"

    create_user = next(s for s in fi.symbols if s.name == "createUser")
    assert create_user.kind == "function"
